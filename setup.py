import os, json
from datasets import load_dataset
from transformers import BertTokenizerFast



def process(orig_data, tokenizer, volumn=34000):
    min_len = 10 
    max_len = 300
    max_diff = 50

    volumn_cnt = 0
    processed = []
    
    for elem in orig_data:
        en_seq, de_seq = elem['en'].lower(), elem['de'].lower()
        en_len, de_len = len(en_seq), len(de_seq)

        #define filtering conditions
        min_condition = (en_len >= min_len) & (de_len >= min_len)
        max_condition = (en_len <= max_len) & (de_len <= max_len)
        dif_condition = abs(en_len - de_len) < max_diff

        if max_condition & min_condition & dif_condition:
            temp_dict = dict()
            
            en_tokenized = tokenizer(en_seq, max_length=max_len, truncation=True)
            de_tokenized = tokenizer(de_seq, max_length=max_len, truncation=True)

            temp_dict['en_ids'] = en_tokenized['input_ids']
            temp_dict['en_mask'] = en_tokenized['attention_mask']
            temp_dict['de_ids'] = de_tokenized['input_ids']
            temp_dict['de_mask'] = de_tokenized['attention_mask']
            
            processed.append(temp_dict)
            
            #End condition
            volumn_cnt += 1
            if volumn_cnt == volumn:
                break

    return processed



def save_data(data_obj):
    #split data into train/valid/test sets
    train, valid, test = data_obj[:-4000], data_obj[-4000:-1000], data_obj[-1000:]
    data_dict = {k:v for k, v in zip(['train', 'valid', 'test'], [train, valid, test])}

    for key, val in data_dict.items():
        with open(f'data/{key}.json', 'w') as f:
            json.dump(val, f)        
        assert os.path.exists(f'data/{key}.json')
    


def main():
    tokenizer = BertTokenizerFast.from_pretrained('prajjwal1/bert-small', model_max_length=128)
    orig = load_dataset('wmt14', 'de-en', split='train')['translation']
    processed = process(orig, tokenizer)
    save_data(processed)



if __name__ == '__main__':
    main()