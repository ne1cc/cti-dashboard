-- Preset-scoped YAML taxonomy condition-group mapping over canonical
-- registry observations.
-- Grain: preset_id + nct_id + snapshot_id + condition_group.
-- Selects one canonical observation per (nct_id, snapshot_date) via
-- int_registry_trial_status_history before expanding condition membership,
-- and keeps conflicting YAML groups keyed by (preset_id, condition_group).
with canonical_observations as (
    select
        nct_id,
        snapshot_id,
        snapshot_date
    from {{ ref('int_registry_trial_status_history') }}
),

preset_members as (
    select
        b.preset_id,
        b.nct_id,
        b.snapshot_id,
        b.snapshot_date,
        b.is_unassigned
    from {{ ref('bridge_preset_trial') }} b
    inner join canonical_observations c
        on b.nct_id = c.nct_id
        and b.snapshot_date = c.snapshot_date
        and b.snapshot_id = c.snapshot_id
    where not b.is_unassigned
),

profile_files as (
    select
        rf.filename,
        string_agg(
            regexp_replace(u.line, '#.*', ''),
            chr(10) order by u.line_no
        ) as clean_content
    from read_text('config/profiles/*.yml') rf,
        unnest(string_split(rf.content, chr(10))) with ordinality as u(line, line_no)
    group by rf.filename
),

profile_tax as (
    select
        regexp_extract(
            clean_content,
            'profile:[^\n]*\n(?:[ \t]+.*\n)*?[ \t]+id:[ \t]*([a-zA-Z0-9_]+)',
            1
        ) as preset_id,
        coalesce(
            nullif(
                regexp_extract(
                    replace(
                        trim(
                            both '"'' ' from regexp_extract(
                                clean_content,
                                'profile:[^\n]*\n(?:[ \t]+.*\n)*?[ \t]+taxonomy:[ \t]*([^\n]+)',
                                1
                            )
                        ),
                        '\',
                        '/'
                    ),
                    '(config/[^/]+)$',
                    1
                ),
                ''
            ),
            trim(
                both '"'' ' from regexp_extract(
                    clean_content,
                    'profile:[^\n]*\n(?:[ \t]+.*\n)*?[ \t]+taxonomy:[ \t]*([^\n]+)',
                    1
                )
            )
        ) as taxonomy_path
    from profile_files
),

tax_files as (
    select
        regexp_extract(replace(filename, '\', '/'), '(config/[^/]+)$', 1) as filename,
        content
    from read_text('config/condition_taxonomy*.yml')
),

tax_lines as (
    select
        t.filename,
        u.line_no,
        regexp_replace(u.line, '#.*', '') as clean_line
    from tax_files t,
        unnest(string_split(t.content, chr(10))) with ordinality as u(line, line_no)
),

parsed_lines as (
    select
        filename,
        line_no,
        case
            when regexp_matches(clean_line, '^default:') then 'default'
            when regexp_matches(clean_line, '^groups:') then 'groups'
            else null
        end as top_section,
        regexp_extract(
            clean_line, '^[ \t]*-[ \t]*condition_group:[ \t]*([a-zA-Z0-9_]+)', 1
        ) as grp_start,
        regexp_extract(
            clean_line, '^[ \t]+condition_group:[ \t]*([a-zA-Z0-9_]+)', 1
        ) as def_grp,
        regexp_extract(
            clean_line, '^[ \t]+dementia_relevance:[ \t]*(true|false)', 1
        ) as dem_rel,
        case
            when regexp_matches(clean_line, '^[ \t]+exact:') then 'exact'
            when regexp_matches(clean_line, '^[ \t]+substrings:') then 'substrings'
            when regexp_matches(clean_line, '^[ \t]*-[ \t]*condition_group:') then 'reset'
            else null
        end as list_kind,
        case
            when regexp_matches(clean_line, '^[ \t]+-[ \t]+')
                and not regexp_matches(clean_line, '^[ \t]*-[ \t]*condition_group:')
            then trim(both '"'' ' from regexp_extract(clean_line, '^[ \t]+-[ \t]+(.*)', 1))
            else null
        end as list_item
    from tax_lines
),

filled as (
    select
        filename,
        line_no,
        last_value(top_section ignore nulls) over (
            partition by filename order by line_no
        ) as section,
        sum(case when grp_start != '' then 1 else 0 end) over (
            partition by filename order by line_no
        ) as grp_ord,
        last_value(nullif(grp_start, '') ignore nulls) over (
            partition by filename order by line_no
        ) as grp_name,
        last_value(list_kind ignore nulls) over (
            partition by filename order by line_no
        ) as cur_list_kind,
        nullif(def_grp, '') as def_grp,
        nullif(dem_rel, '') as dem_rel,
        list_item
    from parsed_lines
),

tax_defaults as (
    select
        filename as taxonomy_path,
        max(def_grp) as default_group,
        coalesce(bool_or(dem_rel = 'true'), false) as default_dementia_relevance
    from filled
    where section = 'default'
    group by filename
),

tax_group_meta as (
    select
        filename as taxonomy_path,
        grp_ord,
        grp_name as condition_group,
        coalesce(bool_or(dem_rel = 'true'), false) as dementia_relevance
    from filled
    where section = 'groups' and grp_ord > 0
    group by filename, grp_ord, grp_name
),

tax_rules as (
    select
        r.filename as taxonomy_path,
        r.grp_ord,
        gm.condition_group,
        gm.dementia_relevance,
        r.cur_list_kind as match_kind,
        lower(trim(r.list_item)) as term
    from filled r
    inner join tax_group_meta gm
        on r.filename = gm.taxonomy_path
        and r.grp_ord = gm.grp_ord
    where r.section = 'groups'
      and r.grp_ord > 0
      and r.list_item is not null
      and r.cur_list_kind in ('exact', 'substrings')
),

preset_conditions as (
    select
        pm.preset_id,
        pm.is_unassigned,
        pm.snapshot_id,
        pm.snapshot_date,
        pm.nct_id,
        sc.condition_raw,
        sc.condition_normalized,
        pt.taxonomy_path,
        td.default_group,
        td.default_dementia_relevance
    from preset_members pm
    inner join {{ ref('stg_registry_trial_conditions') }} sc
        on pm.snapshot_id = sc.snapshot_id
        and pm.nct_id = sc.nct_id
    left join profile_tax pt
        on pm.preset_id = pt.preset_id
        and pt.taxonomy_path != ''
        and pt.taxonomy_path != 'null'
    left join tax_defaults td
        on pt.taxonomy_path = td.taxonomy_path
    where sc.condition_normalized is not null
),

rule_matches as (
    select
        pc.preset_id,
        pc.snapshot_id,
        pc.snapshot_date,
        pc.nct_id,
        pc.condition_raw,
        pc.condition_normalized,
        r.condition_group,
        r.dementia_relevance as dementia_relevance_flag,
        case when r.match_kind = 'exact' then 'high' else 'medium' end as mapping_confidence,
        'yaml_overlay' as mapping_source,
        case when r.match_kind = 'exact' then 1 else 2 end as match_tier,
        r.grp_ord
    from preset_conditions pc
    inner join tax_rules r
        on pc.taxonomy_path = r.taxonomy_path
        and (
            (r.match_kind = 'exact' and pc.condition_normalized = r.term)
            or (r.match_kind = 'substrings' and strpos(pc.condition_normalized, r.term) > 0)
        )
    qualify row_number() over (
        partition by pc.preset_id, pc.snapshot_id, pc.nct_id, pc.condition_raw
        order by match_tier asc, r.grp_ord asc
    ) = 1
),

resolved_conditions as (
    select
        pc.preset_id,
        pc.snapshot_id,
        pc.snapshot_date,
        pc.nct_id,
        pc.condition_raw,
        coalesce(
            rm.condition_group,
            pc.default_group,
            case when pc.is_unassigned then 'unassigned' else 'uncategorized' end
        ) as condition_group,
        coalesce(
            rm.dementia_relevance_flag,
            pc.default_dementia_relevance,
            false
        ) as dementia_relevance_flag,
        coalesce(rm.mapping_confidence, 'low') as mapping_confidence,
        coalesce(
            rm.mapping_source,
            case when pc.is_unassigned then 'unassigned' else 'text_fallback' end
        ) as mapping_source
    from preset_conditions pc
    left join rule_matches rm
        on pc.preset_id = rm.preset_id
        and pc.snapshot_id = rm.snapshot_id
        and pc.nct_id = rm.nct_id
        and pc.condition_raw = rm.condition_raw
)

select
    preset_id,
    snapshot_id,
    snapshot_date,
    nct_id,
    condition_group,
    bool_or(dementia_relevance_flag) as dementia_relevance_flag,
    min(mapping_confidence) as mapping_confidence,
    case
        when bool_or(mapping_source = 'yaml_overlay') then 'yaml_overlay'
        when bool_or(mapping_source = 'unassigned') then 'unassigned'
        else 'text_fallback'
    end as mapping_source,
    count(distinct condition_raw) as source_condition_count
from resolved_conditions
group by 1, 2, 3, 4, 5
