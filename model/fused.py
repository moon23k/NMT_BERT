import torch
import torch.nn as nn
from collections import namedtuple
from transformers import BertModel
from model.simple import (clones, 
                          LayerNorm,
                          Embeddings, 
                          MultiHeadAttention,
                          PositionwiseFeedForward)


class Sublayer(nn.Module):
    def __init__(self, config):
        super(Sublayer, self).__init__()
        self.norm = LayerNorm(config.hidden_dim)
        self.dropout = nn.Dropout(config.dropout_ratio)

    def forward(self, x, sublayer):
        return self.dropout(sublayer(self.norm(x)))



class EncoderLayer(nn.Module):
    def __init__(self, config):
        super(EncoderLayer, self).__init__()
        
        self.self_attn = MultiHeadAttention(config)
        self.bert_attn = MultiHeadAttention(config)
        self.pff = PositionwiseFeedForward(config)

        self.s_sublayer = Sublayer(config)
        self.b_sublayer = Sublayer(config)
        self.p_sublayer = Sublayer(config)


    def forward(self, x, mask, bert_out):
        b = bert_out

        #BERT Attn & Self Attn
        residual = x
        b = self.b_sublayer(x, lambda x: self.bert_attn(x, b, b, mask))
        s = self.s_sublayer(x, lambda x: self.self_attn(x, x, x, mask))
        x = residual + s * 0.5 + b * 0.5  #residual conn

        #Position wise FFN
        residual = x
        x = self.p_sublayer(x, self.pff)
        return residual + x  #residual conn




class DecoderLayer(nn.Module):
    def __init__(self, config):
        super(DecoderLayer, self).__init__()

        self.self_attn = MultiHeadAttention(config)
        self.bert_attn = MultiHeadAttention(config)
        self.enc_dec_attn = MultiHeadAttention(config)
        self.pff = PositionwiseFeedForward(config)

        self.s_sublayer = Sublayer(config)
        self.b_sublayer = Sublayer(config)
        self.e_sublayer = Sublayer(config)
        self.p_sublayer = Sublayer(config)


    def forward(self, x, memory, e_mask, d_mask, bert_out):
        m = memory
        b = bert_out

        #Self Attn
        residual = x
        s = self.s_sublayer(x, lambda x: self.self_attn(x, x, x, d_mask))
        x = residual + s  #residual conn
        

        #BERT Attn & Enc-Dec Attn
        residual = x
        b = self.b_sublayer(x, lambda x: self.bert_attn(x, b, b, e_mask))
        e = self.b_sublayer(x, lambda x: self.bert_attn(x, m, m, e_mask))
        x = residual + b * 0.5 + e * 0.5  #residual conn
        

        #Position wise FFN
        residual = x
        x = self.p_sublayer(x, self.pff)
        return residual + x  #residual conn



class Encoder(nn.Module):
    def __init__(self, config):
        super(Encoder, self).__init__()
        self.emb = Embeddings(config)
        self.norm = LayerNorm(config.hidden_dim)
        self.layers = clones(EncoderLayer(config), config.n_layers)

    def forward(self, x, mask, bert_out):
        x = self.emb(x)
        for layer in self.layers:
            x = layer(x, mask, bert_out)
        return self.norm(x)



class Decoder(nn.Module):
    def __init__(self, config):
        super(Decoder, self).__init__()
        self.emb = Embeddings(config)
        self.norm = LayerNorm(config.hidden_dim)
        self.layers = clones(DecoderLayer(config), config.n_layers)
        

    def forward(self, x, memory, e_mask, d_mask, bert_out):
        x = self.emb(x)
        for layer in self.layers:
            x = layer(x, memory, e_mask, d_mask, bert_out)
        return self.norm(x)



class FusedModel(nn.Module):
    def __init__(self, config):
        super(FusedModel, self).__init__()
        
        self.device = config.device
        self.pad_id = config.pad_id
        self.max_len = config.max_len

        self.bert = BertModel.from_pretrained(config.bert_mname)
        self.encoder = Encoder(config)
        self.decoder = Decoder(config)
        self.fc_out = nn.Linear(config.hidden_dim, config.vocab_size)

        self.criterion = nn.CrossEntropyLoss(ignore_index=config.pad_id, 
                                             label_smoothing=0.1).to(self.device)
        self.outputs = namedtuple('outputs', ('logits', 'loss'))


    def pad_mask(self, x):
        return (x != self.pad_id).unsqueeze(1).unsqueeze(2)


    def dec_mask(self, x):
        seq_len = x.size(-1)
        attn_shape = (1, seq_len, seq_len)
        subsequent_mask = torch.triu(torch.ones(attn_shape), diagonal=1).type(torch.uint8) == 0
        return self.pad_mask(x) & subsequent_mask.to(self.device)


    #Code borrowed from huggingface
    def shift_right(self, labels):
        shifted = labels.new_zeros(labels.shape)
        shifted[:, 1:] = labels[:, :-1].clone()
        shifted[:, 0] = self.pad_id #or self.decoder_start_token_id
        return shifted


    def genreate(self, input_ids, attention_mask):
        batch_size = input_ids.size(0)
        
        e_mask = self.pad_mask(input_ids)
        bert_out = self.bert(input_ids, attention_mask).last_hidden_state
        memory = self.encoder(input_ids, e_mask, bert_out)

        preds = torch.zeros(batch_size, self.max_len).to(self.device)
        for i in range(1, self.max_len):
            d_mask = self.dec_mask(preds)
            dec_out = self.decoder(preds, memory, e_mask, d_mask)
            logits = self.fc_out(dec_out).argmax(-1)

            if logits.sum() == 0:
                break

            preds[i] = logits

        return preds.tolist()


    def forward(self, input_ids, attention_mask, labels):
        shifted_labels = self.shift_right(labels)

        e_mask = self.pad_mask(input_ids), 
        d_mask = self.dec_mask(shifted_labels)
        
        bert_out = self.bert(input_ids, attention_mask).last_hidden_state

        memory = self.encoder(input_ids, e_mask, bert_out)
        d_out = self.decoder(shifted_labels, memory, e_mask, d_mask, bert_out)
        
        logits = self.fc_out(d_out)
        loss = self.criterion(logits.view(-1, self.vocab_size), 
                              labels[:, 1:].view(-1))

        return self.outputs(logits, loss)
