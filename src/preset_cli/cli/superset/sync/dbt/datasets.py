"""
Sync dbt datasets/metrics to Superset.
"""

# pylint: disable=consider-using-f-string

import json
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.engine import create_engine
from sqlalchemy.engine.url import URL as SQLAlchemyURL
from sqlalchemy.engine.url import make_url
from yarl import URL

from preset_cli.api.clients.dbt import MetricSchema, ModelSchema
from preset_cli.api.clients.superset import SupersetClient
from preset_cli.api.operators import OneToMany
from preset_cli.cli.superset.sync.dbt.metrics import (
    get_metric_expression,
    get_metrics_for_model,
)

DEFAULT_CERTIFICATION = {"details": "This table is produced by dbt"}

_logger = logging.getLogger(__name__)


def model_in_database(model: ModelSchema, url: SQLAlchemyURL) -> bool:
    """
    Return if a model is in the same database as a SQLAlchemy URI.
    """
    if url.drivername == "bigquery":
        return model["database"] == url.host

    return model["database"] == url.database


def clean_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """
    Remove incompatbile columns from metatada.
    When updating an existing column/metric we need to remove some fields from the payload.
    """
    return {
        k: v for k, v in metadata.items()
        if k not in {"changed_on", "created_on", "type_generic"}
    }


def create_dataset(
    client: SupersetClient,
    database: Dict[str, Any],
    model: ModelSchema,
) -> Dict[str, Any]:
    """
    Create a physical or virtual dataset.

    Virtual datasets are created when the table database is different from the main
    database, for systems that support cross-database queries (Trino, BigQuery, etc.)
    """
    url = make_url(database["sqlalchemy_uri"])
    if model_in_database(model, url):
        kwargs = {
            "database": database["id"],
            "schema": model["schema"],
            "table_name": model.get("alias") or model["name"],
        }
    else:
        engine = create_engine(url)
        quote = engine.dialect.identifier_preparer.quote
        source = ".".join(quote(model[key]) for key in ("database", "schema", "name"))
        kwargs = {
            "database": database["id"],
            "schema": model["schema"],
            "table_name": model.get("alias") or model["name"],
            "sql": f"SELECT * FROM {source}",
        }

    return client.create_dataset(**kwargs)


def sync_datasets(  # pylint: disable=too-many-locals, too-many-branches, too-many-arguments, too-many-statements # noqa:C901
    client: SupersetClient,
    models: List[ModelSchema],
    metrics: List[MetricSchema],
    database: Any,
    disallow_edits: bool,
    external_url_prefix: str,
    certification: Optional[Dict[str, Any]] = None,
    reload_columns: bool = True,
    merge_metadata: bool = False,
) -> List[Any]:
    """
    Read the dbt manifest and import models as datasets with metrics.
    """
    base_url = URL(external_url_prefix) if external_url_prefix else None

    # add datasets
    datasets = []
    for model in models:
        # load additional metadata from dbt model definition
        model_kwargs = model.get("meta", {}).pop("superset", {})

        try:
            certification_details = model_kwargs["extra"].pop("certification")
        except KeyError:
            certification_details = certification or DEFAULT_CERTIFICATION

        certification_info = {"certification": certification_details}

        filters = {
            "database": OneToMany(database["id"]),
            "schema": model["schema"],
            "table_name": model.get("alias") or model["name"],
        }
        existing = client.get_datasets(**filters)
        if len(existing) > 1:
            raise Exception("More than one dataset found")

        if existing:
            dataset = existing[0]
            _logger.info("Updating dataset %s", model["unique_id"])
        else:
            _logger.info("Creating dataset %s", model["unique_id"])
            try:
                dataset = create_dataset(client, database, model)
            except Exception:  # pylint: disable=broad-except
                _logger.exception("Unable to create dataset")
                continue

        extra = {
            "unique_id": model["unique_id"],
            "depends_on": "ref('{name}')".format(**model),
            **(
                certification_info
                if certification_info["certification"] is not None
                else {}
            ),
            **model_kwargs.pop(
                "extra",
                {},
            ),  # include any additional or custom field specified in model.meta.superset.extra
        }

        dataset_metrics = []
        current_metrics = {}
        model_metrics = {
            metric["name"]: metric for metric in get_metrics_for_model(model, metrics)
        }

        if not reload_columns or merge_metadata:
            current_metrics = {
                metric["metric_name"]: metric
                for metric in client.get_dataset(dataset["id"])["metrics"]
            }
            for name, metric in current_metrics.items():
                # remove data that is not part of the update payload
                metric = clean_metadata(metric)
                if not merge_metadata or name not in model_metrics:
                    dataset_metrics.append(metric)

        for name, metric in model_metrics.items():
            meta = metric.get("meta", {})
            kwargs = meta.pop("superset", {})

            if reload_columns or name not in current_metrics or merge_metadata:
                metric_definition = {
                    "expression": get_metric_expression(name, model_metrics),
                    "metric_name": name,
                    "metric_type": (
                        metric.get("type")  # dbt < 1.3
                        or metric.get("calculation_method")  # dbt >= 1.3
                    ),
                    "verbose_name": metric.get("label", name),
                    "description": metric.get("description", ""),
                    "extra": json.dumps(meta),
                    **kwargs,  # include additional metric metadata defined in metric.meta.superset
                }
                if merge_metadata and name in current_metrics:
                    metric_definition["id"] = current_metrics[name]["id"]
                dataset_metrics.append(metric_definition)

        # update dataset metadata from dbt and clearing metrics
        update = {
            "description": model.get("description", ""),
            "extra": json.dumps(extra),
            "is_managed_externally": disallow_edits,
            "metrics": [] if reload_columns else dataset_metrics,
            **model_kwargs,  # include additional model metadata defined in model.meta.superset
        }
        if base_url:
            fragment = "!/model/{unique_id}".format(**model)
            update["external_url"] = str(base_url.with_fragment(fragment))
        client.update_dataset(dataset["id"], override_columns=reload_columns, **update)

        if reload_columns and dataset_metrics:
            update = {
                "metrics": dataset_metrics,
            }
            client.update_dataset(dataset["id"], override_columns=False, **update)

        # update column descriptions
        if columns := model.get("columns"):
            column_metadata = {column["name"]: column for column in columns}
            current_columns = client.get_dataset(dataset["id"])["columns"]
            for column in current_columns:
                name = column["column_name"]
                if name in column_metadata:
                    column["description"] = column_metadata[name].get("description", "")
                    column["verbose_name"] = column_metadata[name].get("name", "")

                # remove data that is not part of the update payload
                column = clean_metadata(column)

                # for some reason this is being sent as null sometimes
                # https://github.com/preset-io/backend-sdk/issues/163
                if "is_active" in column and column["is_active"] is None:
                    del column["is_active"]

            client.update_dataset(
                dataset["id"],
                override_columns=reload_columns,
                columns=current_columns,
            )

        datasets.append(dataset)

    return datasets
