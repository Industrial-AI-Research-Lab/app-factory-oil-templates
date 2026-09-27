from stairs_sdk.sdk.job_context.job import Job
from stairs_sdk.sdk.job_context.job_context import JobContext

from pydantic import BaseModel

from stairs_resource_model.res_time_model import ResTimeModel
from stairs_storage.adapters.mschm import ModelsAdapter
import os


def _storage():
    url = os.environ.get("RM_ADAPTER_CONN_STR")
    if not url:
        raise RuntimeError("RM_ADAPTER_CONN_STR is required")
    return ModelsAdapter(url=url)

class StairsResourceModelEstimateTimeResult(BaseModel):
    result: int


class StairsResourceModelGetResourcesItem(BaseModel):
    kind: str
    volume: int
    min_count: int
    max_count: int


class StairsResourceModelGetResourcesResult(BaseModel):
    result: list[StairsResourceModelGetResourcesItem]


class StairsResourceModelGetResourcesJob(Job):
    def run(self, job_id: str, ctx: JobContext, **kwargs):
        # Извлечение параметров из kwargs, все ваши параметры должны быть переданы в kwargs
        work_name = kwargs.get("work_name")
        measurement = kwargs.get("measurement")
        volume = kwargs.get("volume")

        db_wrapper = _storage()

        res_time_model = ResTimeModel(dbwrapper=db_wrapper)
        res_data = res_time_model.get_resources_volumes(work_name=work_name, work_volume=volume,
                                                        measurement=measurement)

        # Сохранение результата в Redis
        ctx.result_storage.save_dict(job_id, StairsResourceModelGetResourcesResult(result=res_data["worker_reqs"]).dict())


class StairsResourceModelEstimateTimeJob(Job):
    def run(self, job_id: str, ctx: JobContext, **kwargs):
        # Извлечение параметров из kwargs, все ваши параметры должны быть переданы в kwargs
        work_unit = kwargs.get("work_unit")
        workers = kwargs.get("workers")

        db_wrapper = _storage()
        res_time_model = ResTimeModel(dbwrapper=db_wrapper)

        time = res_time_model.estimate_time(work_unit=work_unit, worker_list=workers, mode='0.5')

        # Сохранение результата в Redis
        ctx.result_storage.save_dict(job_id, StairsResourceModelEstimateTimeResult(result=time).dict())
