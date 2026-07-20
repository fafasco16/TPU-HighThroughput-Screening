from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "配置" / "v0.2多保真准入与权重策略.yaml"
SOURCE_SCOPE_PATH = ROOT / "配置" / "v0.2来源范围.yaml"
ENUM_PATH = ROOT / "配置" / "结构定义" / "v0.2枚举.yaml"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_weight_policy_is_non_operational_until_scientific_gate_closes():
    policy = _load(POLICY_PATH)

    assert policy["policy_version"] == "multi-fidelity-admission-weight-v0.2.12"
    assert policy["policy_status"] == "design_only"
    assert policy["training_enabled"] is False
    assert policy["training_split_created"] is False
    assert policy["training_weight_materialized"] is False
    assert policy["effective_after"] is None
    assert policy["materialization_gate"]["current_gate_status"] == "not_ready"


def test_weight_bands_are_bounded_and_do_not_promote_predictions_or_inputs():
    policy = _load(POLICY_PATH)
    bands = {row["policy_key"]: row for row in policy["default_weight_bands"]}

    for row in bands.values():
        assert 0.0 <= row["minimum_weight"] <= row["maximum_weight"] <= 1.0

    assert bands["model_prediction"]["maximum_weight"] == 0.0
    assert bands["simulation_input_or_code"]["maximum_weight"] == 0.0
    assert bands["experimental_core_raw"]["maximum_weight"] == 1.0
    assert bands["dft_validated_mapping"]["maximum_weight"] < 1.0
    assert bands["experimental_transfer_raw"]["maximum_weight"] < 1.0


def test_curve_points_and_mirrors_cannot_multiply_independent_sample_weight():
    policy = _load(POLICY_PATH)
    principles = policy["principles"]
    curve = policy["curve_weighting"]

    assert principles["minimum_lineage_granularity"] == "formulation_batch_specimen_curve"
    assert principles["independent_weight_unit"] == "specimen"
    assert "independent_scientific_unit" not in principles
    assert principles["curve_points_are_samples"] is False
    assert principles["files_are_samples"] is False
    assert principles["source_mirror_increases_sample_count"] is False
    assert principles["simulation_frames_are_samples"] is False
    assert principles["simulation_restarts_are_independent_without_seed_evidence"] is False
    assert principles["normalize_within_curve"] is True
    assert principles["normalize_within_specimen"] is True
    assert curve["maximum_total_weight_per_specimen"] == 1.0
    assert curve["formulation_condition_total_cap"] == "task_specific_not_materialized"
    assert curve["independent_specimens_increase_statistical_precision"] is True
    assert curve["independent_specimens_increase_chemical_space"] is False
    assert "mirror_duplicate" in policy["hard_zero_conditions"]


def test_observation_identity_keys_cannot_be_inferred_as_split_keys():
    policy = _load(POLICY_PATH)
    semantics = policy["source_override_key_semantics"]

    assert semantics["split_group_keys_required_before_materialization"] is True
    assert semantics["infer_split_group_from_group_keys"] is False
    assert semantics["missing_split_group_action"] == "block_weight_and_split_materialization"


def test_future_weight_formula_is_multiplicative_bounded_and_fail_closed():
    policy = _load(POLICY_PATH)
    materialization = policy["future_weight_materialization"]
    factors = materialization["factors"]

    assert materialization["formula"].startswith(
        "W_obs = I_hard_gate * W_effective_ceiling"
    )
    assert materialization["factor_range"] == [0.0, 1.0]
    assert materialization["clipping_range"] == [0.0, 1.0]
    assert materialization["ceiling_resolution"] == [
        "task_specific_source_override",
        "source_override",
        "default_fidelity_band",
    ]
    assert factors["q_evidence"]["Q5"] == 0.0
    assert factors["q_target"]["out_of_scope"] == 0.0
    assert factors["q_mapping"]["no_auditable_mapping"] == 0.0
    assert factors["q_independence"]["valid_derived_qoi_with_complete_lineage"] > 0.0
    assert factors["q_independence"]["resampled_or_duplicate_copy"] == 0.0
    assert "infer_weight_from_simulation_frame_count" in materialization[
        "forbidden_shortcuts"
    ]


def test_simulation_frames_do_not_outvote_unique_experimental_or_computational_units():
    policy = _load(POLICY_PATH)
    aggregation = policy["simulation_aggregation"]
    bands = {row["policy_key"]: row for row in policy["default_weight_bands"]}

    assert aggregation["independent_unit"] == "unique_system_protocol_seed"
    assert aggregation["maximum_total_weight_per_unique_system_protocol_seed"] == 1.0
    assert (
        aggregation[
            "maximum_total_weight_per_exact_molecular_system_qoi_across_protocols_and_conformers"
        ]
        == 1.0
    )
    assert aggregation["maximum_total_weight_per_formulation_or_system_across_seeds"] == 1.0
    assert aggregation["trajectory_frames_change_resolution_not_total_weight"] is True
    assert aggregation["multiple_seeds_increase_precision_not_chemical_coverage"] is True
    assert aggregation["experimental_calibration_required_for_primary_computational_use"] is True
    assert aggregation["experimental_calibration_required_for_gold_c_reference_admission"] is False
    assert "candidate_rescoring" in aggregation[
        "uncalibrated_reproducible_simulation_allowed_uses"
    ]
    fields = aggregation["minimum_reproducibility_fields_by_method"]
    assert "random_seed_if_stochastic" in fields["md_or_aimd"]
    assert "random_seed_if_stochastic" not in fields["dft"]
    assert "functional_and_basis_set_or_plane_wave_cutoff" in fields["dft"]
    assert bands["md_or_aimd_validated"]["maximum_weight"] < bands[
        "experimental_core_raw"
    ]["maximum_weight"]


def test_gold_reference_admission_includes_reliable_computational_and_virtual_data():
    policy = _load(POLICY_PATH)
    canonical_origins = set(_load(ENUM_PATH)["enums"]["origin_kind"])
    gold = policy["gold_layer_admission"]

    assert gold["Gold-C"]["experimental_mapping_required_for_reference_admission"] is False
    assert {"dft", "md", "finite_element"} <= set(gold["Gold-C"]["admitted_origins"])
    assert "low_fidelity_label" in gold["Gold-C"]["allowed_without_experimental_mapping"]
    assert gold["Gold-V"]["experimental_label_required_for_reference_admission"] is False
    assert "ml_prediction" in gold["Gold-V"]["admitted_origins"]
    assert gold["Gold-V"]["direct_experimental_property_supervision_weight"] == 0.0
    assert "active_learning" in gold["Gold-V"]["allowed_uses"]
    assert set(gold["Gold-C"]["admitted_origins"]) <= canonical_origins
    assert set(gold["Gold-V"]["admitted_origins"]) <= canonical_origins
    assert "physics_surrogate" not in gold["Gold-C"]["admitted_origins"]

    bands = {row["policy_key"]: row for row in policy["default_weight_bands"]}
    unmapped_md = bands["md_or_aimd_unmapped_reference"]
    assert 0.0 < unmapped_md["minimum_weight"] <= unmapped_md["maximum_weight"] <= 0.20
    assert policy["future_weight_materialization"]["factors"]["q_mapping"][
        "simulation_candidate_exact_mapping"
    ] > 0.0


def test_valid_derived_qoi_is_usable_without_becoming_an_independent_specimen():
    policy = _load(POLICY_PATH)
    derived = policy["derived_qoi_handling"]

    assert derived["standalone_target_factor"] > 0.0
    assert derived["independent_sample_increment"] == 0
    assert derived["duplicate_or_resampled_copy_factor"] == 0.0
    assert "parent_curve_lineage" in derived["valid_requirements"]
    assert "derivation_algorithm_and_version" in derived["valid_requirements"]


def test_every_source_override_resolves_to_a_declared_scope_and_mirror_is_zero():
    policy = _load(POLICY_PATH)
    source_config = _load(SOURCE_SCOPE_PATH)
    declared = {row["source_scope_key"] for row in source_config["scopes"]}
    overrides = policy["source_overrides"]
    override_keys = {row["source_scope_key"] for row in overrides}
    by_scope = {row["source_scope_key"]: row for row in overrides}

    assert all(row.get("split_group_keys") for row in overrides)

    assert override_keys <= declared
    assert {
        "scope_drum_05ek6k60_dataset",
        "scope_drum_zf53w893_dataset",
        "scope_qub_83fdb865_dataset",
        "scope_zenodo_17883052_dataset",
        "scope_jagiellonian_tyapfm_dataset",
        "scope_zenodo_20932248_dataset",
        "scope_mendeley_wfsm6f9rbn_v1",
        "scope_mendeley_x6b72k59xn_v1",
        "scope_snd_2024_267_v1",
        "scope_sciencedb_26393_v1",
        "scope_agh_lkhz6q_v1",
        "scope_figshare_31552786_v1",
        "scope_figshare_21716516_v1",
        "scope_figshare_14279117_v1",
        "scope_sciencedb_j00189_00045_dataset",
        "scope_tpu95a_mendeley_local_mirror",
        "scope_materialscloud_vf_ry_dataset",
        "scope_texas_zyq5z1_dataset",
        "scope_mendeley_2sp8fyvhfm_v3",
        "scope_bath_00385_dataset",
        "scope_zenodo_21096098_dataset",
    } <= override_keys
    mirror = next(
        row
        for row in overrides
        if row["source_scope_key"] == "scope_tpu95a_mendeley_local_mirror"
    )
    assert mirror["base_weight_ceiling"] == 0.0
    zero_only = {
        row["source_scope_key"]
        for row in overrides
        if row["base_weight_ceiling"] == 0.0
    }
    assert {
        "scope_tpu95a_mendeley_local_mirror",
        "scope_agh_lkhz6q_v1",
        "scope_bath_00385_dataset",
        "scope_sciencedb_26393_v1",
        "scope_figshare_14279117_v1",
    } <= zero_only
    drum = by_scope["scope_drum_05ek6k60_dataset"]["subdomain_weight_ceilings"]
    assert drum["14bdo_linear_tpu_published_curve"] == 0.65
    assert drum["thermoset_pu_control"] == 0.25
    assert "14bdo_or_thermoset_pu_control" not in drum
    assert by_scope["scope_drum_05ek6k60_dataset"]["split_group_keys"] == [
        "dataset_doi",
        "polymer_series",
        "formulation",
    ]
    assert by_scope["scope_drum_zf53w893_dataset"]["split_group_keys"] == [
        "dataset_doi",
        "polymer_backbone",
        "formulation",
    ]
    assert by_scope["scope_qub_83fdb865_dataset"]["split_group_keys"] == [
        "dataset_doi",
        "formulation",
    ]
    assert by_scope["scope_figshare_21716516_v1"]["base_weight_ceiling"] == 0.20
    dft = by_scope["scope_zenodo_17883052_dataset"]
    assert dft["task_specific_ceilings"] == {
        "experimental_tdeblock_label": 1.00,
        "experimentally_mapped_dft_qoi_or_calibrated_descriptor": 0.50,
        "unmapped_cross_scale_dft_descriptor": 0.25,
        "gaussian_input_unconverged_or_unparsed_output": 0.00,
    }
    assert dft["split_group_keys"] == ["dataset_doi", "molecular_identity"]
    castor = by_scope["scope_figshare_14279117_v1"]
    assert castor["base_weight_ceiling"] == 0.0
    assert castor["future_ceiling_after_verified_nonempty_table_audit"] == 0.10
    images = by_scope["scope_sciencedb_26393_v1"]
    assert images["base_weight_ceiling"] == 0.0
    assert images["future_ceiling_after_manual_labels_and_larger_legal_image_corpus"] == 0.15
    agh = by_scope["scope_agh_lkhz6q_v1"]
    assert agh["base_weight_ceiling"] == 0.0
    assert agh["future_ceiling_after_access_and_full_audit"] == 0.25
    foam = by_scope["scope_mendeley_x6b72k59xn_v1"]
    assert foam["base_weight_ceiling"] == 0.25
    assert "单位相关性能标签触发硬门并取零" in foam["note"]
    jagiellonian = by_scope["scope_jagiellonian_tyapfm_dataset"]
    assert jagiellonian["task_specific_ceilings"][
        "current_element_topology_or_scale_invariant_representation"
    ] == 0.15
    assert jagiellonian["task_specific_ceilings"][
        "current_absolute_distance_geometry_or_hydrogen_bond_qoi"
    ] == 0.0
    printable = by_scope["scope_zenodo_19609901_dataset"]
    assert printable["base_weight_ceiling"] == 0.25
    assert printable["split_group_keys"] == [
        "dataset_doi",
        "base_pu_batch",
        "composite_formulation",
    ]
    shpu = by_scope["scope_figshare_21716516_v1"]
    assert "device_batch_or_figure_panel" in shpu["group_keys"]
    assert "device_batch_or_figure_panel" in shpu["split_group_keys"]
    plant = by_scope["scope_mendeley_2sp8fyvhfm_v3"]
    assert plant["base_weight_ceiling"] == 0.35
    assert plant["split_group_keys"] == [
        "dataset_doi",
        "material_family",
        "temperature_rh",
        "batch",
    ]
    texas = by_scope["scope_texas_zyq5z1_dataset"]
    assert "fiber_csv_id" in texas["group_keys"]
    assert texas["split_group_keys"] == [
        "dataset_doi",
        "material_code",
        "hydration_condition",
        "test_date_batch",
    ]
    vitrimer = by_scope["scope_zenodo_21096098_dataset"]
    assert vitrimer["split_group_keys"] == ["dataset_doi", "formulation"]
    assert vitrimer["task_specific_ceilings"][
        "youngs_modulus_declared_unit_scale_conflict"
    ] == 0.0
    assert vitrimer["task_specific_ceilings"][
        "toughness_declared_unit_or_scale_conflict"
    ] == 0.0


def test_materialization_gate_requires_grouped_split_and_rights_decision():
    policy = _load(POLICY_PATH)
    required = set(policy["materialization_gate"]["required_before_training"])

    assert {
        "source_and_rights_decision_for_training",
        "formulation_batch_specimen_curve_lineage",
        "duplicate_and_equivalence_groups",
        "leakage_group_assignment",
        "simulation_system_qoi_and_cross_seed_weight_caps",
        "train_validation_test_split_by_group",
        "weight_sensitivity_analysis_plan",
        "explicit_source_level_split_group_keys",
    } <= required
    assert {
        "declared_unit_or_scale_conflict_unresolved",
        "coordinate_scale_undeclared_for_absolute_geometry_qoi",
    } <= set(policy["hard_zero_conditions"])


def test_second_batch_source_overrides_preserve_scientific_weight_boundaries():
    policy = _load(POLICY_PATH)
    source_config = _load(SOURCE_SCOPE_PATH)
    declared = {row["source_scope_key"] for row in source_config["scopes"]}
    by_scope = {
        row["source_scope_key"]: row for row in policy["source_overrides"]
    }

    expected_scopes = {
        "scope_zenodo14983287",
        "scope_zenodo6390478",
        "scope_figshare23635998_v1",
        "scope_zenodo15370425",
    }
    assert expected_scopes <= declared
    assert expected_scopes <= set(by_scope)

    fisher = by_scope["scope_fisher_2020_shape_memory_pu_dataset"]
    assert fisher["base_weight_ceiling"] == 0.35
    assert fisher["task_specific_ceilings"]["copied_tail_contamination_point"] == 0.0
    assert fisher["split_group_keys"] == ["dataset_doi", "formulation"]

    lignin = by_scope["scope_zenodo_3631551_lignin_tpu_dataset"]
    assert lignin["base_weight_ceiling"] == 0.20
    assert lignin["task_specific_ceilings"]["precursor_fiber_mechanical_scalar"] == 0.15
    assert lignin["task_specific_ceilings"]["missing_sample_label_or_exact_duplicate_curve"] == 0.0

    elastomer = by_scope["scope_zenodo14983287"]
    assert elastomer["base_weight_ceiling"] == 0.35
    assert elastomer["split_group_keys"] == ["dataset_doi", "material_grade"]
    assert elastomer["task_specific_ceilings"][
        "curve_point_or_response_channel"
    ] == 0.0

    microsphere = by_scope["scope_zenodo6390478"]
    assert microsphere["base_weight_ceiling"] == 0.30
    assert microsphere["split_group_keys"] == [
        "dataset_doi",
        "porosity",
        "specimen_id",
    ]
    assert microsphere["task_specific_ceilings"][
        "post_average_or_smoothed_curve"
    ] == 0.0
    assert microsphere["task_specific_ceilings"][
        "minmax_derived_summary_or_conflicting_xlsx"
    ] == 0.0

    relaxation = by_scope["scope_figshare23635998_v1"]
    assert relaxation["base_weight_ceiling"] == 0.0
    future = relaxation[
        "future_task_specific_ceilings_after_series_level_provenance_materialization"
    ]
    assert future["original_task3_task11_experimental_mechanics"] == 0.25
    assert future["prony_or_abaqus_same_source_model"] == 0.05
    assert future["exact_duplicate_or_alternative_coordinate_view"] == 0.0
    assert relaxation["split_group_keys"] == ["dataset_doi", "material"]

    tpu1301 = by_scope["scope_zenodo15370425"]
    assert tpu1301["base_weight_ceiling"] == 1.00
    assert tpu1301["split_group_keys"] == ["dataset_doi", "material_grade"]
    task_caps = tpu1301["task_specific_ceilings"]
    assert task_caps["direct_raw_tpu_experiment"] == 1.00
    assert task_caps["manually_digitized_tension_curve"] == 0.65
    assert task_caps["mapped_validation_fe_physics_surrogate"] == 0.35
    assert task_caps["calibration_fit_output_for_property_supervision"] == 0.0
    assert task_caps["calibration_output_for_separate_fe_emulator"] == 0.25


def test_third_batch_source_overrides_include_experiments_and_simulations_without_row_inflation():
    policy = _load(POLICY_PATH)
    source_config = _load(SOURCE_SCOPE_PATH)
    declared = {row["source_scope_key"] for row in source_config["scopes"]}
    by_scope = {
        row["source_scope_key"]: row for row in policy["source_overrides"]
    }
    expected = {
        "scope_zenodo_5841610_dataset",
        "scope_zenodo_6128356_dataset",
        "scope_mendeley_hc6npzvw3m_v1",
        "scope_mendeley_dbzdkz95f8_v1",
        "scope_mendeley_kysnxmy7xw_v1",
        "scope_zenodo_7811383_dataset",
        "scope_zenodo_10817092_dataset",
        "scope_zenodo_17790918_dataset",
        "scope_pcl_git_lfs_supplement_local",
        "scope_zenodo_5099589_dataset",
    }
    assert expected <= declared
    assert expected <= set(by_scope)

    commercial = by_scope["scope_zenodo_5841610_dataset"]
    assert commercial["split_group_keys"] == ["dataset_doi", "material_grade"]
    assert commercial["task_specific_ceilings"][
        "figure_s7_70a_85a_material_identity_conflict"
    ] == 0.0
    assert commercial["source_batch_fraction_cap"] == 0.10

    tecoflex = by_scope["scope_zenodo_6128356_dataset"]
    assert tecoflex["split_group_keys"] == ["dataset_doi", "base_tpu_grade"]
    assert tecoflex["task_specific_ceilings"][
        "main_workbook_direct_specimen_scalar"
    ] == 0.55
    assert tecoflex["task_specific_ceilings"][
        "unresolved_strain_unit_curve"
    ] == 0.0

    fatigue = by_scope["scope_mendeley_hc6npzvw3m_v1"]
    assert fatigue["base_weight_ceiling"] == 1.0
    assert fatigue["split_group_keys"] == ["dataset_doi", "material_grade"]
    assert fatigue["task_specific_ceilings"][
        "recovery_history_same_specimen_total_budget"
    ] < fatigue["task_specific_ceilings"]["direct_tpu_specimen_history"]

    lattice = by_scope["scope_mendeley_dbzdkz95f8_v1"]
    assert lattice["task_specific_ceilings"]["claimed_but_unidentifiable_fea_run"] == 0.0
    limited = by_scope["scope_mendeley_kysnxmy7xw_v1"]
    assert limited["maximum_total_weight_for_all_13_simulation_curves"] == 0.05
    assert limited["task_specific_ceilings"]["experiment_average_or_chart_copy"] == 0.0

    input_only = by_scope["scope_zenodo_7811383_dataset"]
    shock_input = by_scope["scope_zenodo_5099589_dataset"]
    assert input_only["base_weight_ceiling"] == 0.0
    assert shock_input["base_weight_ceiling"] == 0.0
    assert input_only[
        "future_task_specific_ceilings_after_output_and_convergence_audit"
    ]["topology_potential_or_simulation_input"] == 0.0
    assert shock_input[
        "future_task_specific_ceilings_after_output_validation_and_rights_closure"
    ]["atomistic_data_force_field_or_input_script"] == 0.0

    dft = by_scope["scope_zenodo_10817092_dataset"]
    assert dft["split_group_keys"] == ["dataset_doi", "reaction_pathway"]
    assert dft["task_specific_ceilings"][
        "converged_pathway_barrier_or_stationary_point_thermochemistry"
    ] == 0.50
    pcl = by_scope["scope_zenodo_17790918_dataset"]
    assert pcl["task_specific_ceilings"][
        "git_lfs_pointer_or_missing_trajectory_object"
    ] == 0.0
    assert pcl["maximum_combined_weight_across_archive_and_lfs_supplement"] == 0.30
    supplement = by_scope["scope_pcl_git_lfs_supplement_local"]
    assert supplement["base_weight_ceiling"] == 0.0
    assert supplement[
        "future_task_specific_ceilings_after_rights_and_convergence_closure"
    ]["within_run_aggregated_conformation_descriptor"] == 0.20
    assert supplement["maximum_combined_weight_with_scope_zenodo_17790918_dataset"] == 0.30
