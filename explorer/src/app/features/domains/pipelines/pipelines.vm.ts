import type {
  ApiDomainEdge,
  ApiDomainEntity,
} from '../../../core/api/api.service';

export interface PipelineJobView {
  job: ApiDomainEntity;
  /** Resolved DEPENDS_ON targets — other jobs in the same workflow. */
  needs: string[];
  /** Textual `needs` references that could not be resolved to jobs. */
  unresolvedNeeds: string[];
  /** DEPLOYS / environment labels declared by the job. */
  deploys: string[];
  /** DAG depth (0 = no dependencies); null when needs are cyclic. */
  level: number | null;
}

export interface PipelineWorkflowView {
  workflow: ApiDomainEntity;
  triggers: string[];
  jobs: PipelineJobView[];
  cyclic: boolean;
}

/**
 * Builds the CI/CD view: cicd_workflow entities owning cicd_job entities
 * (qualified_name convention `<workflow>/jobs/<name>`), needs resolved
 * through DEPENDS_ON edges, triggers from TRIGGERS edges + workflow
 * attributes, and a topological level per job. Unresolved `needs` stay
 * textual — never fabricated.
 */
export function buildPipelinesView(
  entities: readonly ApiDomainEntity[],
  edges: readonly ApiDomainEdge[],
): PipelineWorkflowView[] {
  const workflows = entities
    .filter((e) => e.kind === 'cicd_workflow')
    .sort(byQName);
  const jobs = entities.filter((e) => e.kind === 'cicd_job');
  const byId = new Map(entities.map((e) => [e.id, e]));

  const jobNeeds = new Map<
    number,
    { resolved: Set<string>; unresolved: Set<string> }
  >();
  const jobDeploys = new Map<number, Set<string>>();
  const workflowTriggers = new Map<number, Set<string>>();

  for (const edge of edges) {
    const fromId = edge.from.entity_id;
    if (fromId == null) continue;
    if (edge.type === 'DEPENDS_ON') {
      const entry = jobNeeds.get(fromId) ?? {
        resolved: new Set<string>(),
        unresolved: new Set<string>(),
      };
      const to = byId.get(edge.to.entity_id ?? -1);
      if (to) {
        entry.resolved.add(jobDisplayName(to));
      } else if (edge.to.label) {
        entry.unresolved.add(edge.to.label);
      }
      jobNeeds.set(fromId, entry);
    } else if (edge.type === 'DEPLOYS') {
      const set = jobDeploys.get(fromId) ?? new Set<string>();
      const label =
        edge.to.label ??
        byId.get(edge.to.entity_id ?? -1)?.qualified_name;
      if (label) set.add(label);
      jobDeploys.set(fromId, set);
    } else if (edge.type === 'TRIGGERS') {
      const set = workflowTriggers.get(fromId) ?? new Set<string>();
      const label =
        edge.to.label ??
        byId.get(edge.to.entity_id ?? -1)?.qualified_name;
      if (label) set.add(label);
      workflowTriggers.set(fromId, set);
    }
  }

  return workflows.map((workflow) => {
    const prefix = `${workflow.qualified_name}/jobs/`;
    const wfJobs = jobs
      .filter((j) => j.qualified_name.startsWith(prefix))
      .sort(byQName);

    const triggers = new Set<string>();
    const attrTriggers = workflow.attributes['triggers'];
    if (typeof attrTriggers === 'string' && attrTriggers) {
      for (const t of attrTriggers.split(',')) {
        const trimmed = t.trim();
        if (trimmed) triggers.add(trimmed);
      }
    }
    for (const t of workflowTriggers.get(workflow.id) ?? []) {
      triggers.add(t);
    }

    const views: PipelineJobView[] = wfJobs.map((job) => {
      const needs = jobNeeds.get(job.id);
      return {
        job,
        needs: [...(needs?.resolved ?? [])].sort(),
        unresolvedNeeds: [...(needs?.unresolved ?? [])].sort(),
        deploys: [...(jobDeploys.get(job.id) ?? [])].sort(),
        level: null,
      };
    });
    const cyclic = assignLevels(views);
    return {
      workflow,
      triggers: [...triggers].sort(),
      jobs: views,
      cyclic,
    };
  });
}

/** Short job label: the `jobs/<name>` suffix when present. */
export function jobDisplayName(job: ApiDomainEntity): string {
  const marker = '/jobs/';
  const idx = job.qualified_name.indexOf(marker);
  const base =
    idx >= 0 ? job.qualified_name.slice(idx + marker.length) : job.name;
  return base || job.qualified_name;
}

/**
 * Longest-path DAG levels (0 = no needs). Returns true when the needs
 * graph is cyclic — affected jobs keep level null.
 */
function assignLevels(jobs: PipelineJobView[]): boolean {
  const byName = new Map(jobs.map((j) => [jobDisplayName(j.job), j]));
  const remaining = new Set(jobs);
  const done = new Set<string>();
  let progressed = true;
  while (remaining.size > 0 && progressed) {
    progressed = false;
    for (const j of [...remaining]) {
      const deps = j.needs.filter((n) => byName.has(n));
      const external = j.needs.length > deps.length; // needs outro workflow
      if (external || deps.every((d) => done.has(d))) {
        const levels = deps
          .map((d) => byName.get(d)!.level)
          .filter((l): l is number => l !== null);
        j.level = deps.length === 0 && j.needs.length === 0
          ? 0
          : external && deps.length === 0
            ? 0
            : Math.max(0, ...levels) + 1;
        done.add(jobDisplayName(j.job));
        remaining.delete(j);
        progressed = true;
      }
    }
  }
  return remaining.size > 0;
}

function byQName(a: ApiDomainEntity, b: ApiDomainEntity): number {
  return a.qualified_name.localeCompare(b.qualified_name) || a.id - b.id;
}
