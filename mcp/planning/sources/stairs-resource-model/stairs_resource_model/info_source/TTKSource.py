import numpy as np
import pandas as pd

from stairs_resource_model.info_source.JournalSource import JournalSource


class TTKSource(JournalSource):
    def __init__(self, files):
        super().__init__(files)

    def __repr__(self):
        return f"TTKSource with {len(self.files[0].keys())} measures."

    def __iter__(self):
        for each in self.files[0].items():
            yield each

    def __len__(self):
        return len(self.files[0].keys())

    @staticmethod
    def extract_values_from_bamt_bn(bn, work_index):
        dist = bn.distributions
        resources = list(dist.keys())
        old_root_node = resources.pop(work_index)
        number_of_resources = list()

        for resource_name in resources:
            number_of_resources.append(dist[resource_name]["mean"])

        proportionality_coefs = dist[old_root_node]["mean"]
        if len(resources) != len(proportionality_coefs):
            # broken works
            raise ValueError()

        if any(pd.isna(a) for a in proportionality_coefs):
            raise ValueError()

        regressor = np.asarray(proportionality_coefs)
        return regressor, resources

    @classmethod
    def create_from_json_bamt(cls, data, work_index):
        """Creates source from json, with"""
        try:
            from bamt_light.networks import ContinuousBN
        except ImportError:
            raise ImportError("Please install BAMT light to use this.")

        files = []

        indices = [] # collect indices like (measure_name, work_name)
        work_names = set(data.keys())

        for work_name in work_names:
            for mes_name in data[work_name].keys():
                indices.append((work_name, mes_name))

        df = pd.DataFrame.from_records(indices, columns=["work_name", "mes_name"])
        aggregated_indices = df.groupby("mes_name").agg(list)

        parsed_info = {}
        for measure_name, work_names_list in aggregated_indices.iterrows():
            parsed_info[measure_name] = {}
            for work_name in work_names_list.values[0]:
                bn = ContinuousBN()
                bn.load(data[work_name][measure_name])
                try:
                    regressors, resources = cls.extract_values_from_bamt_bn(bn, work_index)
                except Exception as e:
                    continue

                if any(regressors > 1000):
                    continue

                parsed_info[measure_name][work_name] = {}
                parsed_info[measure_name][work_name]["regressors"] = regressors
                parsed_info[measure_name][work_name]["resources"] = resources

        files.append(parsed_info)
        source = cls(files)
        return source

    @classmethod
    def create_from_json(cls, data):
        """Creates source from json, with"""
        raise NotImplemented()