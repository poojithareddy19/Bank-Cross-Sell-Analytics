from __future__ import annotations

import pandas as pd
import pytest

from xsell.db import connect
from xsell.features import ID_COLUMNS, LABEL_COLUMNS, SEGMENT_COLUMN, build_features, load_dataset
from xsell.train import candidate_models

CUTOFF = 5  # feature month 2015-06 (the validation month)


def load_all(config) -> pd.DataFrame:
    with connect(config, read_only=True) as connection:
        return connection.execute("SELECT * FROM propensity_dataset ORDER BY customer_id, feature_month").df()


def scramble_months_after(config, cutoff: int) -> None:
    """Change every product flag, activity, segment, age and income after the cutoff month."""
    flags = list(config.product_codes)
    flips = ", ".join(f"{code} = 1 - {code}" for code in flags)
    with connect(config) as connection:
        connection.execute(
            f"UPDATE fact_monthly_holdings SET {flips}, is_active = 1 - coalesce(is_active, 0), "
            f"segment = 'scrambled', n_products = 24 - n_products WHERE month_index > {cutoff}"
        )
        connection.execute(
            f"UPDATE stg_holdings SET income = coalesce(income, 0) * 10 + 1, age = age + 50 "
            f"WHERE month_index > {cutoff}"
        )


def feature_part(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.drop(columns=[*LABEL_COLUMNS]).reset_index(drop=True)


def test_every_feature_month_precedes_its_label_month(model_warehouse):
    build_features(model_warehouse)
    frame = load_all(model_warehouse)
    assert len(frame) > 0
    assert (frame["feature_month"] < frame["label_month"]).all()
    assert (frame["label_month"] == frame["feature_month"] + 1).all()


def test_target_flag_and_labels_are_not_features(model_warehouse):
    build_features(model_warehouse)
    dataset = load_dataset(model_warehouse, "product")
    target = model_warehouse.model.target_product
    assert target not in dataset.feature_columns
    assert not set(dataset.feature_columns) & {*LABEL_COLUMNS, *ID_COLUMNS, SEGMENT_COLUMN}
    assert len(dataset.product_flags) == 23


def test_features_do_not_change_when_later_months_change(model_warehouse):
    build_features(model_warehouse)
    before = load_all(model_warehouse)
    scramble_months_after(model_warehouse, CUTOFF)
    build_features(model_warehouse)
    after = load_all(model_warehouse)

    early_before = before[before["feature_month"] <= CUTOFF]
    early_after = after[after["feature_month"] <= CUTOFF]
    assert len(early_before) > 1000
    pd.testing.assert_frame_equal(feature_part(early_before), feature_part(early_after))
    # The labels of the cutoff month come from cutoff + 1, which was scrambled: they must move.
    at_cutoff = before["feature_month"] == CUTOFF
    assert not before.loc[at_cutoff, "adopts_target"].equals(
        after.loc[after["feature_month"] == CUTOFF, "adopts_target"]
    )


def test_scrambling_the_feature_month_does_change_features(model_warehouse):
    """Control for the test above: the comparison can detect a change."""
    build_features(model_warehouse)
    before = load_all(model_warehouse)
    scramble_months_after(model_warehouse, CUTOFF - 1)
    build_features(model_warehouse)
    after = load_all(model_warehouse)
    month = before["feature_month"] == CUTOFF
    with pytest.raises(AssertionError):
        pd.testing.assert_frame_equal(
            feature_part(before[month]), feature_part(after[after["feature_month"] == CUTOFF])
        )


def test_preprocessing_is_fitted_on_training_rows_only(model_warehouse):
    build_features(model_warehouse)
    dataset = load_dataset(model_warehouse, "product")
    x_train, y_train = dataset.xy("train")
    pipeline = candidate_models(model_warehouse, dataset.product_flags, positive_weight=1.0)["logistic_regression"]
    pipeline.fit(x_train, y_train)
    numeric = pipeline.named_steps["preprocess"].named_transformers_["numeric"]
    learned_medians = numeric.named_steps["impute"].statistics_
    assert learned_medians == pytest.approx(x_train[list(numeric.feature_names_in_)].median().to_numpy())
    encoder = pipeline.named_steps["preprocess"].named_transformers_["category"]
    for column, categories in zip(encoder.feature_names_in_, encoder.categories_, strict=True):
        assert set(categories) <= set(x_train[column])
