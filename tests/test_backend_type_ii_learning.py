from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from src.Backend.type_ii_detection import CANDIDATE_FEATURE_SCHEMA_VERSION
from src.Backend.type_ii_learning import (
    TypeIILearningLibrary,
    TypeIITrainingExample,
    TypeIITrainingPolicy,
    load_active_type_ii_model,
    record_type_ii_example,
    train_type_ii_model,
)


def _example(index: int, label: int) -> TypeIITrainingExample:
    rng = np.random.default_rng(index)
    features = rng.normal(-2.0 if label == 0 else 2.0, 0.15, 14)
    snr = rng.normal(0.0, 0.4, (12, 20)).astype(np.float32)
    mask = None
    band = None
    review_kind = "rejected"
    if label:
        mask = np.zeros_like(snr, dtype=bool)
        mask[4:8, 3:17] = True
        snr[mask] += 5.0
        band = "fundamental" if index % 2 else "harmonic"
        review_kind = "teach"
    return TypeIITrainingExample.create(
        source_hash=f"source-{label}-{index}",
        label=label,
        review_kind=review_kind,
        station="TEST",
        band=band,
        feature_vector=features,
        snr_crop=snr if label else None,
        mask=mask,
        metadata={"reviewed_duration_s": 900.0},
    )


def test_library_round_trip_and_export_import(tmp_path):
    library = TypeIILearningLibrary(tmp_path / "library")
    positive = _example(1, 1)
    negative = _example(1, 0)
    record_type_ii_example(positive, library)
    record_type_ii_example(negative, library)

    assert library.counts() == {"positive": 1, "negative": 1, "sources": 2, "total": 2}
    restored = library.examples()
    assert {item.example_id for item in restored} == {positive.example_id, negative.example_id}
    assert restored[0].feature_schema_version == CANDIDATE_FEATURE_SCHEMA_VERSION

    package = tmp_path / "backup.ecallisto-typeii"
    library.export_package(package)
    imported = TypeIILearningLibrary(tmp_path / "imported")
    assert imported.import_package(package) == 2
    assert imported.import_package(package) == 0
    assert imported.counts()["total"] == 2


def test_training_promotes_valid_model_and_loads_numeric_arrays(tmp_path):
    library = TypeIILearningLibrary(tmp_path / "learning")
    for label in (0, 1):
        for index in range(12):
            library.add(_example(index, label))

    policy = TypeIITrainingPolicy(
        min_positive_examples=8,
        min_negative_examples=8,
        min_validation_positive_groups=1,
        min_validation_negative_groups=1,
        min_precision=0.80,
        min_recall=0.80,
        max_false_candidates_per_15m=0.5,
        min_mask_iou=0.30,
        min_ridge_coverage=0.60,
        station_min_positive=4,
        station_min_negative=4,
    )
    outcome = train_type_ii_model(library, policy=policy)

    assert outcome.active_model_changed is True
    assert outcome.manifest.promoted is True
    assert outcome.manifest.metrics["precision"] >= 0.80
    model = load_active_type_ii_model(library)
    assert model is not None
    assert model.predict_candidate(np.full(14, 2.0), station="TEST") > 0.5
    pointer = json.loads(library.active_manifest_path.read_text(encoding="utf-8"))
    arrays_path = library.models_dir / f"{pointer['model_id']}.npz"
    with np.load(arrays_path, allow_pickle=False) as arrays:
        assert "candidate_coef" in arrays
    arrays_path.write_bytes(arrays_path.read_bytes() + b"tampered")
    assert load_active_type_ii_model(library) is None


def test_default_group_split_reserves_five_validation_sources_per_class(tmp_path):
    library = TypeIILearningLibrary(tmp_path / "learning")
    for label in (0, 1):
        for source_index in range(6):
            for repeat in range(4):
                example = _example(source_index * 10 + repeat, label)
                library.add(
                    TypeIITrainingExample(
                        example_id=example.example_id,
                        source_hash=f"source-{label}-{source_index}",
                        label=example.label,
                        review_kind=example.review_kind,
                        station=example.station,
                        event_id=example.event_id,
                        band=example.band,
                        created_at=example.created_at,
                        feature_schema_version=example.feature_schema_version,
                        feature_vector=example.feature_vector,
                        snr_crop=example.snr_crop,
                        mask=example.mask,
                        metadata=example.metadata,
                    )
                )

    outcome = train_type_ii_model(library)

    assert outcome.manifest.metrics["validation_positive_groups"] >= 5
    assert outcome.manifest.metrics["validation_negative_groups"] >= 5
    assert outcome.manifest.promoted is True
    assert library.active_manifest() is not None


def test_underfilled_library_is_staged_without_replacing_active_model(tmp_path):
    library = TypeIILearningLibrary(tmp_path / "learning")
    library.add(_example(1, 1))
    library.add(_example(1, 0))

    outcome = train_type_ii_model(library)

    assert outcome.active_model_changed is False
    assert outcome.manifest.promoted is False
    assert "Need at least" in outcome.manifest.status_reason
    assert not library.active_manifest_path.exists()


def test_library_is_independent_of_install_location(tmp_path):
    persistent_root = tmp_path / "application-data" / "type_ii_learning"
    first_install = TypeIILearningLibrary(persistent_root)
    first_install.add(_example(2, 1))

    # A replacement executable points to the same per-user application-data root.
    replacement_install = TypeIILearningLibrary(persistent_root)
    assert replacement_install.counts()["positive"] == 1
    assert Path(replacement_install.database_path).is_file()
