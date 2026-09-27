from stairs_resource_model.info_source.BaseSource import BaseSource
from typing import List, Dict

class JournalSource(BaseSource):
    def __init__(self, files: list):
        super().__init__(files)

    def collect(self, work_name:str, unit:str) -> List[Dict]:
        data = self.files[0]
        if unit not in data:
            raise Exception('Journal does not contain unit '+unit)
        else:
            data_by_unit = data[unit]
            if work_name not in data_by_unit:
                raise Exception('Journal does not contain work '+work_name)
            else:
                work_data = data_by_unit[work_name]
                return work_data