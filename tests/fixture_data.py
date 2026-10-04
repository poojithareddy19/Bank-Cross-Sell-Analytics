"""Synthetic Santander-style holdings with planted, hand-checked answers.

60 customers over 8 monthly snapshots (2015-01-28 to 2015-08-28), written in
the same raw text format as train_ver2.csv: padded numbers, mixed indrel_1mes
codes, NA flags, a quoted province containing a comma.

Planted customers (month index m0 = 2015-01):
  1001  adopts credit card (ind_tjcr_fin_ult1) at m3
  1002  drops e-account (ind_ecue_fin_ult1) at m4
  1003  absent at m3; direct debit 0 at m2 and 1 at m4 -> NOT an adoption (gap)
  1004  income empty (NULL)
  1005  income "         NA" (text), province "CORUÑA, A"
  1006  seniority (antiguedad) -999999
  1007  ind_nomina_ult1 and ind_nom_pens_ult1 are NA at m2 (2 flags filled)
  1008  joins 2015-03-05, first seen m2; adopts payroll account and payroll at m5
  1009  joins 2015-01-20, last seen m4 (join cohort 2015-01 loses it from m5)
  1010  active, payroll + current account + direct debit (rule baseline shape)
  1011  age 115 (impossible)
  1012  join date (fecha_alta) missing
  2002  adopts e-account at m2, then credit card at m5 (transition e-account -> credit card)
  2003  adopts e-account at m2, then direct debit at m4 (transition e-account -> direct debit)
  2005  joins 2015-01-10, present every month (join cohort 2015-01)
  2041-2048  inactive (ind_actividad_cliente 0) every month
  other 2001-2048  constant holdings, present every month, no events
"""

from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path

MONTHS = [f"2015-{month:02d}-28" for month in range(1, 9)]

ATTRIBUTE_COLUMNS = [
    "fecha_dato", "ncodpers", "ind_empleado", "pais_residencia", "sexo", "age", "fecha_alta",
    "ind_nuevo", "antiguedad", "indrel", "ult_fec_cli_1t", "indrel_1mes", "tiprel_1mes", "indresi",
    "indext", "conyuemp", "canal_entrada", "indfall", "tipodom", "cod_prov", "nomprov",
    "ind_actividad_cliente", "renta", "segmento",
]  # fmt: skip
PRODUCT_COLUMNS = [
    "ind_ahor_fin_ult1", "ind_aval_fin_ult1", "ind_cco_fin_ult1", "ind_cder_fin_ult1",
    "ind_cno_fin_ult1", "ind_ctju_fin_ult1", "ind_ctma_fin_ult1", "ind_ctop_fin_ult1",
    "ind_ctpp_fin_ult1", "ind_deco_fin_ult1", "ind_deme_fin_ult1", "ind_dela_fin_ult1",
    "ind_ecue_fin_ult1", "ind_fond_fin_ult1", "ind_hip_fin_ult1", "ind_plan_fin_ult1",
    "ind_pres_fin_ult1", "ind_reca_fin_ult1", "ind_tjcr_fin_ult1", "ind_valo_fin_ult1",
    "ind_viv_fin_ult1", "ind_nomina_ult1", "ind_nom_pens_ult1", "ind_recibo_ult1",
]  # fmt: skip
COLUMNS = ATTRIBUTE_COLUMNS + PRODUCT_COLUMNS

# Mixed spellings of indrel_1mes, cycled by month, as seen in the real file.
INDREL_1MES_VALUES = ["1", "1.0", "1", "2", "P", "3.0", "1", "4.0"]

# Known answers. Tests assert against these literal values, not recomputed ones.
EXPECTED = {
    "rows": 474,
    "customers": 60,
    "months": 8,
    "rows_per_month": {
        "2015-01": 59, "2015-02": 59, "2015-03": 60, "2015-04": 59,
        "2015-05": 60, "2015-06": 59, "2015-07": 59, "2015-08": 59,
    },
    "adoptions": {
        (1001, "ind_tjcr_fin_ult1", "2015-04"),
        (1008, "ind_cno_fin_ult1", "2015-06"),
        (1008, "ind_nomina_ult1", "2015-06"),
        (2002, "ind_ecue_fin_ult1", "2015-03"),
        (2002, "ind_tjcr_fin_ult1", "2015-06"),
        (2003, "ind_ecue_fin_ult1", "2015-03"),
        (2003, "ind_recibo_ult1", "2015-05"),
    },
    "attritions": {(1002, "ind_ecue_fin_ult1", "2015-05")},
    "gap_customers": {1003},
    # Flag changes across the 1003 gap (m2 -> m4) that must not count as events.
    "changes_across_gaps": {(1003, "ind_recibo_ult1", "2015-05")},
    # (from product, to product): (customers, months between adoptions)
    "transitions": {
        ("ind_ecue_fin_ult1", "ind_tjcr_fin_ult1"): (1, 3),
        ("ind_ecue_fin_ult1", "ind_recibo_ult1"): (1, 2),
    },
    # join month: (cohort size, customers retained at k = 0, 1, ... months after joining)
    "cohorts": {
        "2015-01": (2, [2, 2, 2, 2, 2, 1, 1, 1]),
        "2015-03": (1, [1, 1, 1, 1, 1, 1]),
    },
    "flags_filled_from_null": 2,
    # Latest month (2015-08): products held -> (customers, active customers)
    "engagement_by_products": {"1": (41, 35), "2": (14, 12), "3": (4, 4)},
    # Latest month: product families held -> customers
    "families_held": {"1": 41, "2": 16, "3+": 2},
    # Latest month holders of selected products (59 customers present)
    "latest_holders": {"ind_cco_fin_ult1": 59, "ind_tjcr_fin_ult1": 2, "ind_recibo_ult1": 15},
}  # fmt: skip


def _base_row(customer_id: int) -> dict[str, str]:
    row = dict.fromkeys(COLUMNS, "")
    row.update(
        ncodpers=str(customer_id),
        ind_empleado="N",
        pais_residencia="ES",
        sexo="H" if customer_id % 2 else "V",
        age=f"{25 + customer_id % 40:>3}",
        fecha_alta="2012-08-10",
        ind_nuevo=" 0",
        antiguedad=f"{30 + customer_id % 50:>7}",
        indrel=" 1",
        tiprel_1mes="A",
        indresi="S",
        indext="N",
        canal_entrada=["KHE", "KAT", "KFC"][customer_id % 3],
        indfall="N",
        tipodom=" 1",
        cod_prov="28",
        nomprov="MADRID",
        ind_actividad_cliente=" 1",
        renta=f"{40000 + customer_id * 13.7:.2f}",
        segmento="02 - PARTICULARES",
    )
    for product in PRODUCT_COLUMNS:
        row[product] = "0"
    row["ind_cco_fin_ult1"] = "1"
    return row


def _customer_rows(customer_id: int) -> list[dict[str, str]]:
    present = range(8)
    if customer_id == 1003:
        present = [0, 1, 2, 4, 5, 6, 7]
    elif customer_id == 1008:
        present = range(2, 8)
    elif customer_id == 1009:
        present = range(5)

    rows = []
    for month_index in present:
        row = _base_row(customer_id)
        row["fecha_dato"] = MONTHS[month_index]
        row["indrel_1mes"] = INDREL_1MES_VALUES[month_index]
        if customer_id == 1001 and month_index >= 3:
            row["ind_tjcr_fin_ult1"] = "1"
        elif customer_id == 1002:
            row["ind_ecue_fin_ult1"] = "1" if month_index < 4 else "0"
        elif customer_id == 1003:
            row["ind_recibo_ult1"] = "1" if month_index >= 3 else "0"
        elif customer_id == 1004:
            row["renta"] = ""
        elif customer_id == 1005:
            row["renta"] = "         NA"
            row["cod_prov"] = "15"
            row["nomprov"] = "CORUÑA, A"
        elif customer_id == 1006:
            row["antiguedad"] = "-999999"
        elif customer_id == 1007 and month_index == 2:
            row["ind_nomina_ult1"] = "NA"
            row["ind_nom_pens_ult1"] = "NA"
        elif customer_id == 1008:
            row["fecha_alta"] = "2015-03-05"
            row["ind_nuevo"] = " 1"
            row["antiguedad"] = f"{max(month_index - 2, 0):>7}"
            if month_index >= 5:
                row["ind_cno_fin_ult1"] = "1"
                row["ind_nomina_ult1"] = "1"
        elif customer_id == 1010:
            row["ind_nomina_ult1"] = "1"
            row["ind_recibo_ult1"] = "1"
        elif customer_id == 1011:
            row["age"] = "115"
        elif customer_id == 1012:
            row["fecha_alta"] = ""
        elif customer_id == 1009:
            row["fecha_alta"] = "2015-01-20"
        elif customer_id == 2002:
            row["ind_ecue_fin_ult1"] = "1" if month_index >= 2 else "0"
            row["ind_tjcr_fin_ult1"] = "1" if month_index >= 5 else "0"
        elif customer_id == 2003:
            row["ind_ecue_fin_ult1"] = "1" if month_index >= 2 else "0"
            row["ind_recibo_ult1"] = "1" if month_index >= 4 else "0"
        elif customer_id == 2005:
            row["fecha_alta"] = "2015-01-10"
        elif customer_id >= 2001 and customer_id % 4 == 0:
            row["ind_recibo_ult1"] = "1"
        if 2041 <= customer_id <= 2048:
            row["ind_actividad_cliente"] = " 0"
        rows.append(row)
    return rows


def customer_ids() -> list[int]:
    return list(range(1001, 1013)) + list(range(2001, 2049))


def fixture_rows() -> list[dict[str, str]]:
    """All rows ordered by month then customer, like the real file."""
    rows = [row for customer_id in customer_ids() for row in _customer_rows(customer_id)]
    return sorted(rows, key=lambda row: (row["fecha_dato"], int(row["ncodpers"])))


def fixture_csv_text(rows: list[dict[str, str]] | None = None, columns: list[str] | None = None) -> str:
    columns = columns or COLUMNS
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows if rows is not None else fixture_rows())
    return buffer.getvalue()


def write_fixture_zip(path: Path, member: str = "train_ver2.csv", csv_text: str | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member, csv_text if csv_text is not None else fixture_csv_text())
    return path


MODEL_CHANNELS = [f"K{letter}{letter}" for letter in "ABCDEFGHIJKLMNO"]  # 15 channels, skewed below


def model_fixture_rows(n_customers: int = 2000, seed: int = 7) -> list[dict[str, str]]:
    """Larger synthetic data with a learnable credit-card signal, for the propensity model.

    Each month a customer without a credit card adopts one with a probability that rises
    with holding an e-account, being active and being under 35. The rule baseline
    (active, payroll, 3+ products) does not use that signal, so a model can beat it.
    About 5% of customers join late and 5% leave early. Seeded, so fully reproducible.
    """
    import numpy as np

    rng = np.random.default_rng(seed)
    channel_weights = np.linspace(15, 1, len(MODEL_CHANNELS))
    channel_weights /= channel_weights.sum()
    rows = []
    for number in range(n_customers):
        customer_id = 100_000 + number
        first = int(rng.integers(1, 4)) if rng.random() < 0.05 else 0
        last = int(rng.integers(4, 7)) if rng.random() < 0.05 else 7
        age = int(rng.integers(18, 81))
        channel = str(rng.choice(MODEL_CHANNELS, p=channel_weights))
        sex = "H" if rng.random() < 0.5 else "V"
        segment = str(rng.choice(["01 - TOP", "02 - PARTICULARES", "03 - UNIVERSITARIO"], p=[0.05, 0.6, 0.35]))
        income = "" if rng.random() < 0.1 else f"{rng.lognormal(11, 0.5):.2f}"
        active = rng.random() < 0.6
        holds = dict.fromkeys(PRODUCT_COLUMNS, 0)
        holds["ind_cco_fin_ult1"] = int(rng.random() < 0.9)
        holds["ind_recibo_ult1"] = int(rng.random() < 0.3)
        holds["ind_ecue_fin_ult1"] = int(rng.random() < 0.2)
        payroll = int(rng.random() < 0.15)
        holds["ind_cno_fin_ult1"] = holds["ind_nomina_ult1"] = holds["ind_nom_pens_ult1"] = payroll
        holds["ind_tjcr_fin_ult1"] = int(rng.random() < 0.1)
        for month_index in range(8):
            if month_index > 0:
                if rng.random() < 0.05:
                    active = not active
                if not holds["ind_ecue_fin_ult1"] and rng.random() < 0.03:
                    holds["ind_ecue_fin_ult1"] = 1
                if holds["ind_tjcr_fin_ult1"]:
                    holds["ind_tjcr_fin_ult1"] = int(rng.random() >= 0.03)
                else:
                    chance = 0.005 + 0.12 * holds["ind_ecue_fin_ult1"] + 0.03 * active + 0.04 * (age < 35)
                    holds["ind_tjcr_fin_ult1"] = int(rng.random() < chance)
            if not first <= month_index <= last:
                continue
            row = dict.fromkeys(COLUMNS, "")
            row.update(
                fecha_dato=MONTHS[month_index],
                ncodpers=str(customer_id),
                ind_empleado="N",
                pais_residencia="ES",
                sexo=sex,
                age=f"{age + month_index // 12:>3}",
                fecha_alta="2014-06-01" if first == 0 else f"2015-{first + 1:02d}-03",
                ind_nuevo=" 1" if first > 0 else " 0",
                antiguedad=f"{12 + number % 100 + month_index:>7}",
                indrel=" 1",
                indrel_1mes="1",
                tiprel_1mes="A" if active else "I",
                indresi="S",
                indext="N",
                canal_entrada=channel,
                indfall="N",
                tipodom=" 1",
                cod_prov="28",
                nomprov="MADRID",
                ind_actividad_cliente=" 1" if active else " 0",
                renta=income,
                segmento=segment,
            )
            row.update({code: str(value) for code, value in holds.items()})
            rows.append(row)
    return sorted(rows, key=lambda row: (row["fecha_dato"], int(row["ncodpers"])))
