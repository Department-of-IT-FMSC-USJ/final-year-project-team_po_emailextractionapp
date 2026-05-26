"""Train the PO classifier — TF-IDF + Logistic Regression.

Training/evaluation architecture (in order):

  1. **Load + clean.** Read every label, drop records where both the
     subject and body are empty (nothing to learn from).
  2. **Deduplicate** by normalized (subject + body) text — the same
     forwarded PO often lands in the inbox multiple times with
     distinct email IDs, which otherwise leaks copies between
     train and test. Newest ``received_at`` / ``labeled_at`` wins
     on a collision.
  3. **Sort by time** (``received_at`` if present, else
     ``labeled_at``) so newer emails fall at the end of the list.
  4. **Time-based train / test split.** The newest ~20% becomes the
     **final test set** — never seen by the classifier during fitting
     or CV. The older ~80% is the **train pool**. Refuses to train
     if either side of the split loses a class.
  5. **Cross-validation lives inside the train pool only.** Used as a
     model-selection / stability check, never as the headline number.
     When ≥80% of the train pool carries a ``from_addr`` group key,
     uses ``StratifiedGroupKFold`` so emails from the same supplier
     never straddle a fold (catches "same template" leakage). Falls
     back to ``StratifiedKFold`` otherwise.
  6. **Fit a single model on the train pool**, score it on the final
     test set. Those test numbers are the *headline* accuracy /
     precision / recall / F1 / confusion matrix.
  7. **Misclassified test examples** — FP / FN cases are written into
     the metadata for manual review.
  8. **Split before preprocessing.** TF-IDF lives inside the sklearn
     ``Pipeline`` that every fold/split passes to ``.fit()``, so the
     vocabulary, IDF weights, and stop-word list never see the
     evaluation slice.

The model artifact saved to ``model.joblib`` is the one trained on the
**train pool only** — the headline metrics describe the actual model
that gets deployed, not a separate "deploy on 100%" variant.
"""

import json
import re
from datetime import datetime, timezone
from typing import Any

import joblib
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import confusion_matrix, precision_recall_fscore_support
from sklearn.model_selection import (
    StratifiedGroupKFold,
    StratifiedKFold,
    cross_val_predict,
    cross_val_score,
)
from sklearn.pipeline import Pipeline

from classifier.dataset import NOT_PO_LABEL, PO_LABEL, load_labels
from classifier.features import build_feature_text
from classifier.loader import METADATA_FILENAME, MODEL_FILENAME
from config.settings import settings

# Minimum labels required per class before a model can be trained.
MIN_PER_CLASS = 3
# Target number of CV folds; clamped down if either class has fewer
# samples (or fewer distinct groups when group-based CV kicks in).
DEFAULT_CV_FOLDS = 5
# Fraction of records reserved as the held-out final test set.
TEST_FRAC = 0.2
# Use group-based CV when at least this share of the train pool
# carries a ``from_addr`` value.
GROUP_CV_COVERAGE = 0.8
# Cap the number of misclassified test examples kept in metadata.
MISCLASSIFIED_KEEP = 20


class NotEnoughData(RuntimeError):
    """Raised when there are too few labels to train a usable model."""


def _build_pipeline() -> Pipeline:
    return Pipeline(
        [
            (
                "tfidf",
                TfidfVectorizer(
                    lowercase=True,
                    ngram_range=(1, 2),
                    min_df=1,
                    max_features=20000,
                    sublinear_tf=True,
                    stop_words="english",
                ),
            ),
            ("clf", LogisticRegression(max_iter=1000, class_weight="balanced")),
        ]
    )


_WHITESPACE = re.compile(r"\s+")


def _dedup_key(subject: str, body_text: str) -> str:
    return _WHITESPACE.sub(" ", f"{subject}\n{body_text}".strip().lower())


def _sort_key(record: dict[str, Any]) -> str:
    """Prefer the email's actual receive time over the label-click time."""
    return record.get("received_at") or record.get("labeled_at") or ""


def _has_text(record: dict[str, Any]) -> bool:
    return bool(
        (record.get("subject") or "").strip() or (record.get("body_text") or "").strip()
    )


def _deduplicate(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Drop records whose normalized text matches a later one.

    Newest-by-time wins on a collision (uses :func:`_sort_key`). The
    return order matches the input order minus dropped duplicates, so
    downstream sort-by-time stays correct.
    """
    by_recency = sorted(
        enumerate(records), key=lambda pair: _sort_key(pair[1]), reverse=True
    )
    seen: set[str] = set()
    kept_indices: list[int] = []
    for original_idx, record in by_recency:
        key = _dedup_key(record.get("subject", ""), record.get("body_text", ""))
        if key in seen:
            continue
        seen.add(key)
        kept_indices.append(original_idx)
    kept_indices.sort()
    kept = [records[i] for i in kept_indices]
    return kept, len(records) - len(kept)


def _classification_metrics(
    y_true: list[str], y_pred: list[str], label_order: list[str]
) -> dict[str, Any]:
    precision, recall, f1, support = precision_recall_fscore_support(
        y_true, y_pred, labels=label_order, zero_division=0
    )
    macro_p, macro_r, macro_f1, _ = precision_recall_fscore_support(
        y_true, y_pred, labels=label_order, average="macro", zero_division=0
    )
    return {
        "macro_precision": float(macro_p),
        "macro_recall": float(macro_r),
        "macro_f1": float(macro_f1),
        "per_class": {
            label: {
                "precision": float(precision[i]),
                "recall": float(recall[i]),
                "f1": float(f1[i]),
                "support": int(support[i]),
            }
            for i, label in enumerate(label_order)
        },
        "confusion_matrix": confusion_matrix(
            y_true, y_pred, labels=label_order
        ).tolist(),
        "confusion_matrix_labels": label_order,
    }


def _build_misclassified(
    records: list[dict[str, Any]],
    y_true: list[str],
    y_pred: list[str],
    y_proba: list[float],
) -> list[dict[str, Any]]:
    """Return the per-record FP / FN list with prediction confidence."""
    out: list[dict[str, Any]] = []
    for record, true_label, pred_label, confidence in zip(
        records, y_true, y_pred, y_proba, strict=True
    ):
        if pred_label == true_label:
            continue
        if pred_label == PO_LABEL and true_label == NOT_PO_LABEL:
            kind = "false_positive"  # Non-PO predicted as PO
        elif pred_label == NOT_PO_LABEL and true_label == PO_LABEL:
            kind = "false_negative"  # PO predicted as Non-PO
        else:
            kind = "other"
        out.append({
            "email_id": record.get("email_id", ""),
            "subject": (record.get("subject") or "")[:160],
            "from_addr": record.get("from_addr"),
            "received_at": record.get("received_at"),
            "true_label": true_label,
            "predicted_label": pred_label,
            "confidence": round(float(confidence), 4),
            "kind": kind,
        })
    # Lowest-confidence mistakes first — they're the ones most likely
    # to recur and most informative to label.
    out.sort(key=lambda row: row["confidence"])
    return out[:MISCLASSIFIED_KEEP]


def train_classifier() -> dict[str, Any]:
    """Run the full evaluation + training pipeline.

    Raises :class:`NotEnoughData` when the corpus can't support a
    realistic train / test split.
    """
    raw_records = load_labels()

    # 1+2. Drop blanks, then dedupe.
    with_text = [r for r in raw_records if _has_text(r)]
    n_empty_dropped = len(raw_records) - len(with_text)
    records, n_duplicates_dropped = _deduplicate(with_text)

    n_po = sum(1 for r in records if r.get("label") == PO_LABEL)
    n_not_po = sum(1 for r in records if r.get("label") == NOT_PO_LABEL)
    if n_po < MIN_PER_CLASS or n_not_po < MIN_PER_CLASS:
        raise NotEnoughData(
            f"Need at least {MIN_PER_CLASS} PO and {MIN_PER_CLASS} non-PO labels "
            f"to train (have {n_po} PO, {n_not_po} non-PO after cleaning)."
        )

    # 3. Time sort — oldest first, newest last.
    records.sort(key=_sort_key)
    n_with_received_at = sum(1 for r in records if r.get("received_at"))
    if n_with_received_at == len(records):
        time_basis = "received_at"
    elif n_with_received_at == 0:
        time_basis = "labeled_at"
    else:
        time_basis = "mixed"

    # 4. Time-based train pool / final test split (newest 20% as test).
    n_test = max(2, int(round(len(records) * TEST_FRAC)))
    if n_test >= len(records):
        raise NotEnoughData("test split would leave no training data — add more labels.")
    train_pool = records[: len(records) - n_test]
    test_set = records[len(records) - n_test :]

    def _has_both_classes(rs: list[dict[str, Any]]) -> bool:
        return any(r.get("label") == PO_LABEL for r in rs) and any(
            r.get("label") == NOT_PO_LABEL for r in rs
        )

    if not _has_both_classes(train_pool):
        raise NotEnoughData(
            "training pool (oldest 80%) is missing one class — "
            "the corpus is too time-skewed to train honestly."
        )
    if not _has_both_classes(test_set):
        raise NotEnoughData(
            "final test set (newest 20%) is missing one class — "
            "label some recent examples of both classes."
        )

    label_order = [PO_LABEL, NOT_PO_LABEL]
    train_texts = [
        build_feature_text(r.get("subject", ""), r.get("body_text", "")) for r in train_pool
    ]
    train_targets = [r.get("label", "") for r in train_pool]
    test_texts = [
        build_feature_text(r.get("subject", ""), r.get("body_text", "")) for r in test_set
    ]
    test_targets = [r.get("label", "") for r in test_set]

    # 5. Cross-validation on the train pool ONLY. Group-based when we
    #    have enough sender info; otherwise stratified random folds.
    train_groups = [r.get("from_addr") or "" for r in train_pool]
    n_with_group = sum(1 for g in train_groups if g)
    distinct_groups = len({g for g in train_groups if g})
    use_groups = (
        n_with_group >= int(GROUP_CV_COVERAGE * len(train_pool))
        and distinct_groups >= 2
    )

    train_n_po = train_targets.count(PO_LABEL)
    train_n_not_po = train_targets.count(NOT_PO_LABEL)
    max_class_folds = min(train_n_po, train_n_not_po)
    if use_groups:
        n_folds = max(2, min(DEFAULT_CV_FOLDS, distinct_groups, max_class_folds))
        cv: Any = StratifiedGroupKFold(
            n_splits=n_folds, shuffle=True, random_state=42
        )
        cv_strategy = "stratified_group_kfold_by_sender"
        cv_groups: list[str] | None = train_groups
    else:
        n_folds = max(2, min(DEFAULT_CV_FOLDS, max_class_folds))
        cv = StratifiedKFold(n_splits=n_folds, shuffle=True, random_state=42)
        cv_strategy = "stratified_kfold"
        cv_groups = None

    fold_accuracies = cross_val_score(
        _build_pipeline(),
        train_texts,
        train_targets,
        cv=cv,
        scoring="accuracy",
        groups=cv_groups,
    )
    fold_f1_macros = cross_val_score(
        _build_pipeline(),
        train_texts,
        train_targets,
        cv=cv,
        scoring="f1_macro",
        groups=cv_groups,
    )
    y_oof = cross_val_predict(
        _build_pipeline(),
        train_texts,
        train_targets,
        cv=cv,
        groups=cv_groups,
    )
    oof_metrics = _classification_metrics(train_targets, list(y_oof), label_order)

    # 6. Fit the actual deployed model on the train pool only.
    model = _build_pipeline()
    model.fit(train_texts, train_targets)
    train_pool_accuracy = float(model.score(train_texts, train_targets))

    # 7. Score on the held-out test set — these are the headline numbers.
    test_pred = list(model.predict(test_texts))
    test_proba = model.predict_proba(test_texts)
    classes = list(model.classes_)
    # Confidence = probability assigned to the predicted class.
    test_confidence = [
        float(test_proba[i, classes.index(test_pred[i])]) for i in range(len(test_pred))
    ]
    test_accuracy = float(np.mean(np.array(test_pred) == np.array(test_targets)))
    test_metrics = _classification_metrics(test_targets, test_pred, label_order)
    misclassified = _build_misclassified(
        test_set, test_targets, test_pred, test_confidence
    )

    model_dir = settings.classifier_model_path
    model_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, model_dir / MODEL_FILENAME)

    cv_acc_mean = float(np.mean(fold_accuracies))
    cv_acc_std = float(np.std(fold_accuracies))
    cv_f1_mean = float(np.mean(fold_f1_macros))
    cv_f1_std = float(np.std(fold_f1_macros))

    metadata = {
        "model_version": settings.classifier_model_version,
        "algorithm": "tfidf+logreg",
        "trained_at": datetime.now(timezone.utc).isoformat(),

        # Dataset hygiene.
        "n_labels_raw": len(raw_records),
        "n_empty_dropped": n_empty_dropped,
        "n_duplicates_dropped": n_duplicates_dropped,
        "n_samples": len(records),
        "n_po": n_po,
        "n_not_po": n_not_po,
        "n_with_received_at": n_with_received_at,
        "n_with_from_addr": sum(1 for r in records if r.get("from_addr")),

        # Splits.
        "split_strategy": "time_based_train80_test20",
        "time_basis": time_basis,
        "n_train_pool": len(train_pool),
        "n_test_holdout": len(test_set),
        "train_pool_time_max": _sort_key(train_pool[-1]) if train_pool else "",
        "test_holdout_time_min": _sort_key(test_set[0]) if test_set else "",

        # Cross-validation (model-selection signal on train pool only).
        "cv_strategy": cv_strategy,
        "cv_folds": int(n_folds),
        "cv_group_coverage": n_with_group,
        "cv_distinct_groups": distinct_groups,
        "cv_accuracy_mean": cv_acc_mean,
        "cv_accuracy_std": cv_acc_std,
        "cv_accuracy_per_fold": [float(s) for s in fold_accuracies],
        "cv_f1_macro_mean": cv_f1_mean,
        "cv_f1_macro_std": cv_f1_std,
        "cv_f1_macro_per_fold": [float(s) for s in fold_f1_macros],
        "cv_oof": {**oof_metrics, "n": len(train_pool)},

        # Held-out test set — the HEADLINE numbers.
        "test_accuracy": test_accuracy,
        "test": {
            "n": len(test_set),
            "accuracy": test_accuracy,
            **test_metrics,
        },
        "misclassified_test": misclassified,
        "n_misclassified_test": sum(
            1 for p, t in zip(test_pred, test_targets, strict=True) if p != t
        ),

        # In-sample fit of the deployed model on its training data (sanity check
        # — a value far below CV mean would signal training instability).
        "train_accuracy": train_pool_accuracy,
        # Back-compat keys for older UI/tooling — n_train was renamed to
        # n_train_pool; map it across so dashboards don't blank out.
        "n_train": len(train_pool),
        "n_test": len(test_set),
        "split": "time_based_train80_test20",
        "macro_precision": test_metrics["macro_precision"],
        "macro_recall": test_metrics["macro_recall"],
        "macro_f1": test_metrics["macro_f1"],
        "per_class": test_metrics["per_class"],
        "confusion_matrix": test_metrics["confusion_matrix"],
        "confusion_matrix_labels": test_metrics["confusion_matrix_labels"],
    }
    (model_dir / METADATA_FILENAME).write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return metadata


def attach_metadata_fields(extra: dict[str, Any]) -> dict[str, Any]:
    """Merge ``extra`` into the saved metadata.json and return the result.

    Used by API callers that want to record post-train observations
    (e.g. the unseen-inbox sanity check) on top of the metadata that
    :func:`train_classifier` just wrote. Silently returns ``{}`` when
    no metadata file exists yet.
    """
    meta_path = settings.classifier_model_path / METADATA_FILENAME
    if not meta_path.exists():
        return {}
    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    metadata.update(extra)
    meta_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return metadata
