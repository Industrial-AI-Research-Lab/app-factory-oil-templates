from stairs_resource_model.info_source.BaseSource import BaseSource
from typing import List, Dict
import pandas as pd

class KSGSource(BaseSource):
    def __init__(self, files: list):
        super().__init__(files)

    def collect(self, work_name:str, unit:str) -> List[Dict]:
        data = pd.DataFrame(columns=['Total Volume','Volume per day', 'Time'])
        possible_mes = []
        possible_work_match = 0
        for file in self.files:
            for work in file['tasks']:
                if (work['granular_name'] == work_name) | (work['task_name'] == work_name):
                    possible_work_match += 1
                    possible_mes.append(work['task_measurement'])
                    if work['task_measurement'] == unit:
                        total_volume = float(work['task_act_volume'])
                        if total_volume != 0:
                            if len(work['resources_by_days']) > 1:
                                not_null_day = []
                                for el in work['resources_by_days']:
                                    if float(el['volume']) != 0:
                                        not_null_day.append(el)
                                for el in not_null_day:
                                    df_dict = {'Total Volume':[total_volume],'Volume per day':[float(el['volume'])], 'Time':[len(not_null_day)+1]}
                                df_dict = {'Total Volume':[total_volume],'Volume per day':[total_volume/(len(not_null_day)+1)], 'Time':[len(not_null_day)+1]}
                                data = pd.concat([data, pd.DataFrame.from_dict(df_dict)])
                            else:
                                df_dict = {'Total Volume':[total_volume],'Volume per day':[total_volume/(float(work['actual_duration'])+1)], 'Time':[float(work['actual_duration'])+1]}
                                data = pd.concat([data, pd.DataFrame.from_dict(df_dict)])
        if possible_work_match == 0:
            print('No data for work '+work_name)
            print('-------------')
        if data.shape[0] < 1:
            if possible_mes:
                if unit not in possible_mes:
                    print('Work ' + work_name+' does not math unit '+unit+', possible units '+str(possible_mes))
                    print('-------------')
        return data


