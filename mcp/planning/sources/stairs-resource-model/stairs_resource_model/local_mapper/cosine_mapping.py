import math
import re
import pandas as pd



standard_masurements = ['%',
 'РРС',
 'ЦСП',
 'антенна',
 'га',
 'дерево',
 'знак',
 'жил',
 'заземлитель',
 'импульсная линия',
 'испытание',
 'измерение',
 'место',
 'кабель',
 'кг',
 'км',
 'км2',
 'пром',
 'кВт',
 'коммутатор',
 'комплект',
 'котел',
 'котлован',
 'кт',
 'лист',
 'м',
 'м2',
 'м3',
 'мп',
 'конец',
 'опора',
 'образ',
 'фундамент',
 'отметка',
 'переход',
 'рабочая плеть',
 'рез',
 'секция',
 'система',
 'соединение',
 'стык',
 'т',
 'труба',
 'узел',
 'установка',
 'участок',
 'формир',
 'шт',
 'вех',
 'лунка',
 'штатив',
 'рейс']


helping_dict = {'тн':'т', 'м':'м', 'м.':'м', 'проход кабеля':'кабель', 'конец кабеля':'кабель', 'т':'т', 'ст':'стык', '%':'%', 'к-т':'кт','п.м':'мп','ПК+':'комплект','Шт.':'шт','ВУ':'установка','котл':'котлован','ст.':'стык','м 3':'м3','пер':'переход','уст.':'установка','уч':'участок','Фунд.':'фундамент','отм.':'отметка','изм.':'измерение','мест':'место','котл.':'котлован',
                'пер.':'переход','уст.':'установка','уч':'участок','изм.':'измерение',' мест':'место',' изм.':'измерение',
       'м.п .':'мп', 'ком':'комплект', 'ст .':'стык', 'шт ( 1 км )':'шт', 'м.п.':'мп'}




from collections import Counter


WORD = re.compile(r"\w+")


def get_cosine(vec1, vec2):
    intersection = set(vec1.keys()) & set(vec2.keys())
    numerator = sum([vec1[x] * vec2[x] for x in intersection])

    sum1 = sum([vec1[x] ** 2 for x in list(vec1.keys())])
    sum2 = sum([vec2[x] ** 2 for x in list(vec2.keys())])
    denominator = math.sqrt(sum1) * math.sqrt(sum2)

    if not denominator:
        return 0.0
    else:
        return float(numerator) / denominator


def text_to_vector(text):
    words = WORD.findall(text)
    return Counter(words)

def cosine_mapping(words, to_what_map):
    result_df = pd.DataFrame(columns=['Activity Name', 'Matched File Name', 'Cosine'])
    for word in words:
        start_cos = 0
        start_word = ''
        for st_word in to_what_map:
            dist = get_cosine(text_to_vector(word), text_to_vector(st_word))
            if dist > start_cos:
                start_cos = dist
                start_word = st_word
        result_df = pd.concat([result_df, pd.DataFrame.from_dict({'Activity Name':[word], 'Matched File Name':[start_word], 'Cosine':[start_cos]})])
    return result_df


def maping_measurements_names(mes):
    df = cosine_mapping(mes, standard_masurements)
    temp_dict = dict(zip(df['Activity Name'], df['Matched File Name']))
    df['Matched File Name'] = df['Activity Name'].apply(lambda x: helping_dict[x] if x in helping_dict else temp_dict[x])
    return df

