#! -*- coding: utf-8 -*-
# 预训练语料构建

import numpy as np
import tensorflow as tf
from bert4keras.utils import parallel_apply


class TrainingDataset:
    """MLM预训练数据集生成器（roberta模式）
    """
    def __init__(self,
                 tokenizer,
                 word_segment,
                 mask_rate=0.15,
                 padding_length=512):
        """参数说明：
            tokenizer必须是bert4keras自带的tokenizer类；
            word_segment是任意分词函数。
        """
        self.tokenizer = tokenizer
        self.word_segment = word_segment
        self.mask_rate = mask_rate
        self.padding_length = padding_length
        self.token_cls_id = tokenizer._token_dict['[CLS]']
        self.token_sep_id = tokenizer._token_dict['[SEP]']

    def sentence_process(self, text):
        """单个文本的处理函数
        流程：分词，然后转id，按照mask_rate构建全词mask的序列
              来指定哪些token是否要被mask
        """
        words = self.word_segment(text)
        rands = np.random.random(len(words))

        tokens, mask_ids = [], []
        for rand, word in zip(rands, words):
            is_mask = 1 if rand <= self.mask_rate else 0
            word_tokens = self.tokenizer.tokenize(word,
                                                  add_cls=False,
                                                  add_sep=False)
            tokens.extend(word_tokens)
            mask_ids.extend([is_mask] * len(word_tokens))

        token_ids = self.tokenizer.tokens_to_ids(tokens)

        return token_ids, mask_ids

    def padding(self, sequence):
        """对单个序列进行补0
        """
        sequence = sequence[:self.padding_length]
        return sequence + [0] * (self.padding_length - len(sequence))

    def paragraph_process(self, texts):
        """texts是单句组成的list
        做法：不断塞句子，直到长度最接近padding_length，然后补0。
        """
        results = []
        token_ids, mask_ids = [self.token_cls_id], [0]

        for text in texts:
            # 处理单个句子
            _token_ids, _mask_ids = self.sentence_process(text)
            _token_ids = _token_ids[:self.padding_length - 2]
            _mask_ids = _mask_ids[:self.padding_length - 2]
            # 如果长度即将溢出
            if len(token_ids) + len(_token_ids) > self.padding_length - 1:
                # 插入终止符
                token_ids.append(self.token_sep_id)
                mask_ids.append(0)
                # padding到指定长度
                token_ids = self.padding(token_ids)
                mask_ids = self.padding(mask_ids)
                # 存储结果，并开始构建新的样本
                results.append((token_ids, mask_ids))
                token_ids, mask_ids = [self.token_cls_id], [0]
            token_ids.extend(_token_ids)
            mask_ids.extend(_mask_ids)

        return results

    def tfrecord_serialize(self, results):
        """转为tfrecord的字符串，等待写入到文件
        """
        new_results = []
        for token_ids, mask_ids in results:
            features = {
                'token_ids': tf.train.Feature(int64_list=tf.train.Int64List(value=token_ids)),
                'mask_ids': tf.train.Feature(int64_list=tf.train.Int64List(value=mask_ids)),
            }
            tf_features = tf.train.Features(feature=features)
            tf_example = tf.train.Example(features=tf_features)
            tf_serialized = tf_example.SerializeToString()
            new_results.append(tf_serialized)

        return new_results

    def process(self, corpus, record_name, workers=8, max_queue_size=2000):
        """处理输入语料（corpus），最终转为tfrecord格式（record_name）
        自带多进程支持，如果cpu核心数多，请加大workers和max_queue_size。
        """
        writer = tf.io.TFRecordWriter(record_name)
        globals()['count'] = 0

        def write_to_tfrecord(results):
            globals()['count'] += len(results)
            for tf_serialized in results:
                writer.write(tf_serialized)

        def paragraph_process(texts):
            results = self.paragraph_process(texts)
            results = self.tfrecord_serialize(results)
            return results

        parallel_apply(
            func=paragraph_process,
            iterable=corpus,
            workers=workers,
            max_queue_size=max_queue_size,
            callback=write_to_tfrecord,
        )

        writer.close()
        print('write %s examples into %s' % (count, record_name))

    @staticmethod
    def load_tfrecord(record_names, padding_length, batch_size):
        """加载处理成tfrecord格式的语料
        """
        if not isinstance(record_names, list):
            record_names = [record_names]

        dataset = tf.data.TFRecordDataset(record_names)

        # 解析函数
        def _parse_function(example_proto):
            features = {
                'token_ids': tf.io.FixedLenFeature([padding_length], tf.int64),
                'mask_ids': tf.io.FixedLenFeature([padding_length], tf.int64),
            }
            parsed_features = tf.io.parse_single_example(example_proto, features)
            return parsed_features['token_ids'], parsed_features['mask_ids']

        dataset = dataset.map(_parse_function) # 解析
        dataset = dataset.repeat() # 循环
        dataset = dataset.shuffle(batch_size * 1000) # 打乱
        dataset = dataset.batch(batch_size) # 成批

        return dataset


if __name__ == '__main__':

    # 使用测试

    from bert4keras.utils import Tokenizer
    import json
    import jieba_fast as jieba
    from tqdm import tqdm

    dict_path = '/root/kg/bert/chinese_L-12_H-768_A-12/vocab.txt'
    tokenizer = Tokenizer(dict_path)
    padding_length = 256

    def some_texts():
        with open('../../baike.items') as f:
            for l in f:
                yield json.loads(l).split('\n')

    def word_segment(text):
        return jieba.lcut(text)

    TD = TrainingDataset(tokenizer, word_segment, padding_length=256)
    TD.process(tqdm(some_texts()), '../../test.tfrecord')