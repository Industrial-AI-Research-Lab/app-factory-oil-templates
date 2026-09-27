import logging
from itertools import chain
from operator import attrgetter
from random import Random
from typing import Type, Any

from sampo.schemas import WorkTimeEstimator, WorkUnit, Worker, WorkerReq, WorkEstimationMode, WorkerProductivityMode
from sampo.schemas.time import Time
from sampo.utilities.collections_util import build_index
from stairs_resource_model.res_time_model import ResTimeModel
from stairs_storage import MschmAdapter

from config import Database as DatabaseConfig
from DAL.models2.basic.enums import BasicResourceModelEnum
from models.utility.model_name import coerce_model_name
from scheduler.work_estimator.exceptions import ResourceModelKeyError, ResourceModelRequirementsError

SERVICE_WORKS = ["Начало работ по марке", "Окончание работ по марке", "NaN", "start of project", "finish of project"]

DEFAULT_MODEL_NAME, HISTORICAL_MODEL_NAME = BasicResourceModelEnum.Standard.model_to_string(), BasicResourceModelEnum.Historical.model_to_string()

gp_url = DatabaseConfig.RM_ADAPTER_CONN_STR

model = ResTimeModel(MschmAdapter(url=gp_url))
gp_adapter = MschmAdapter(url=gp_url)

logger = logging.getLogger(__name__)


class FieldDevWorkEstimator(WorkTimeEstimator):
    def __init__(self, rand: Random = Random(), resource_model_type: str = DEFAULT_MODEL_NAME):
        self._url = gp_url
        self._model = model
        self._gp_adapter = gp_adapter
        self._resource_model_type = resource_model_type
        self._use_idle = True
        self._estimation_mode = WorkEstimationMode.Realistic
        self.rand = rand
        self._productivity_mode = WorkerProductivityMode.Static

    @staticmethod
    def _normalize_model_name(
            model_name: dict[str, Any] | str | None,
            fallback_name: str | None = None,
            fallback_measurement: str | None = None,
    ) -> dict[str, Any]:
        parsed = coerce_model_name(model_name)

        resolved_name = parsed.get("name") or parsed.get("granular_name") or fallback_name
        if resolved_name is None or str(resolved_name).strip() == "":
            raise ResourceModelKeyError("model_name.name is missing")

        return {
            "name": str(resolved_name),
            "category": "" if parsed.get("category") is None else str(parsed.get("category")),
            "measurement": (
                ""
                if (parsed.get("measurement") or fallback_measurement) is None
                else str(parsed.get("measurement") or fallback_measurement)
            ),
        }

    def estimate_time(self, work_unit: WorkUnit, worker_list: list[Worker]):
        # w_u = {
        #     "name": work_unit.name.split("_stage_")[0],
        #     "volume": work_unit.volume,
        #     "measurement": work_unit.volume_type,
        # }
        w_u = self._normalize_model_name(
            work_unit.model_name,
            fallback_name=work_unit.name,
            fallback_measurement=getattr(work_unit, "volume_type", None),
        )
        w_u['volume'] = work_unit.volume
        work_name = str(w_u['name']).split("_stage_")[0]
        w_u['name'] = work_name

        w_l = [{"name": w.name, "_count": w.count} for w in worker_list]
        name2worker = build_index(worker_list, attrgetter("name"))

        current_model_type = self._resource_model_type

        match self._estimation_mode:
            case WorkEstimationMode.Optimistic:
                mode_str = "0.1"
            case WorkEstimationMode.Realistic:
                mode_str = "0.5"
            case _:
                mode_str = "0.9"

        for res_req in work_unit.worker_reqs:
            if name2worker.get(res_req.kind, None) is None:
                w_l.append({"name": res_req.kind, "_count": 0})
        if work_name in SERVICE_WORKS:
            return Time(0)



        if current_model_type == HISTORICAL_MODEL_NAME and not self._gp_adapter.get_perf_model(
                name=work_name,
                category=w_u['category'],
                model_type=current_model_type,
                measurement_type=w_u['measurement'],
        ):
            current_model_type = DEFAULT_MODEL_NAME
            if not self._gp_adapter.get_perf_model(
                    name=work_name,
                    category=w_u['category'],
                    model_type=current_model_type,
                    measurement_type=w_u['measurement'],
            ):
                raise ResourceModelKeyError(
                    f"No standard and historical models in DB for work, unit,  category, model type: "
                    f"{work_name}, {w_u['measurement']}, {w_u['category']}, {current_model_type}"
                )

        try:
            estimate_timed = Time(
                int(
                    self._model.estimate_time(
                        work_unit=w_u,
                        worker_list=w_l,
                        mode=mode_str,
                        model_type=current_model_type
                    )
                )
            )
            return estimate_timed
        except Exception as e:
            logger.warning(f"Couldn't estimate time for work unit with name='{work_name}': {e}")
            raise e

    def find_work_resources(
            self,
            model_name: dict[str, Any],
            work_volume: float,
            resource_name: list[str] | None = None
    ) -> list[WorkerReq]:
        model_name = self._normalize_model_name(model_name)
        work_name = str(model_name['name'])

        if work_name in SERVICE_WORKS:
            return []

        current_model_type = self._resource_model_type

        if current_model_type == HISTORICAL_MODEL_NAME and not self._gp_adapter.get_perf_model(
                category=model_name['category'],
                name=work_name,
                measurement_type=model_name['measurement'],
                model_type=current_model_type,
        ):
            current_model_type = DEFAULT_MODEL_NAME
            if not self._gp_adapter.get_perf_model(
                    category=model_name['category'],
                    name=work_name,
                    measurement_type=model_name['measurement'],
                    model_type=current_model_type,
            ):
                raise ResourceModelKeyError(
                    f"No standard and historical models in DB for work, unit, model type: "
                    f"{work_name}, {model_name['measurement']}, {current_model_type}"
                )

        worker_req_dict = self._model.get_resources_volumes(
            category=model_name['category'],
            work_name=work_name,
            work_volume=work_volume,
            measurement=model_name['measurement'],
            model_type=current_model_type,
        )

        worker_reqs = []

        for worker_req_list in worker_req_dict.values():
            req_items = []
            for req in worker_req_list:
                if req['max_count'] < req['min_count']:
                    raise ResourceModelRequirementsError(
                        f'Incorrect assessment by the model '
                        f'min_count: {req["min_count"]} / max_count: {req["max_count"]}'
                    )
                req_items.append(
                    WorkerReq(
                        kind=req['kind'],
                        volume=Time(req['volume']),
                        min_count=req['min_count'],
                        max_count=req['max_count'],
                    )
                )
            worker_reqs.append(req_items)

        return list(chain.from_iterable(worker_reqs))


    def set_estimation_mode(self, use_idle: bool = True, mode: WorkEstimationMode = WorkEstimationMode.Realistic):
        self._use_idle = use_idle
        self._estimation_mode = mode

    def set_productivity_mode(self, mode: WorkerProductivityMode = WorkerProductivityMode.Static):
        self._productivity_mode = mode

    def get_recreate_info(self) -> tuple[Type, tuple]:
        return FieldDevWorkEstimator, tuple(self._url)

    def get_model_name_keys(self) -> list[str]:
        return ['name', 'measurement']
