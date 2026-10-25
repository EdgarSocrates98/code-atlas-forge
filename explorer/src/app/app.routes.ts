import { Routes } from '@angular/router';

export const routes: Routes = [
  { path: '', pathMatch: 'full', redirectTo: 'dashboard' },
  {
    path: 'dashboard',
    loadComponent: () =>
      import('./features/dashboard/dashboard.component').then((m) => m.DashboardComponent),
    title: 'Painel · Code Atlas Explorer',
  },
  {
    path: 'repositories',
    loadComponent: () =>
      import('./features/repositories/repositories.component').then(
        (m) => m.RepositoriesComponent,
      ),
    title: 'Repositórios · Code Atlas Explorer',
  },
  {
    path: 'graph',
    loadComponent: () => import('./features/graph/graph.component').then((m) => m.GraphComponent),
    title: 'Grafo · Code Atlas Explorer',
  },
  {
    path: 'search',
    loadComponent: () =>
      import('./features/search/search.component').then((m) => m.SearchComponent),
    title: 'Busca · Code Atlas Explorer',
  },
  {
    path: 'symbols/:id',
    loadComponent: () =>
      import('./features/symbols/symbols.component').then((m) => m.SymbolsComponent),
    title: 'Símbolo · Code Atlas Explorer',
  },
  {
    path: 'impact',
    loadComponent: () =>
      import('./features/impact/impact.component').then((m) => m.ImpactComponent),
    title: 'Impacto · Code Atlas Explorer',
  },
  {
    path: 'revisions',
    loadComponent: () =>
      import('./features/revisions/revisions.component').then((m) => m.RevisionsComponent),
    title: 'Revisões · Code Atlas Explorer',
  },
  {
    path: 'context',
    loadComponent: () =>
      import('./features/context/context.component').then((m) => m.ContextComponent),
    title: 'Context Packs · Code Atlas Explorer',
  },
  {
    path: 'lineage',
    loadComponent: () =>
      import('./features/domains/lineage/lineage.component').then(
        (m) => m.LineageComponent,
      ),
    title: 'Linhagem · Code Atlas Explorer',
  },
  {
    path: 'contracts',
    loadComponent: () =>
      import('./features/domains/contracts/contracts.component').then(
        (m) => m.ContractsComponent,
      ),
    title: 'Contratos · Code Atlas Explorer',
  },
  {
    path: 'infra',
    loadComponent: () =>
      import('./features/domains/infra/infra.component').then(
        (m) => m.InfraComponent,
      ),
    title: 'Infraestrutura · Code Atlas Explorer',
  },
  {
    path: 'pipelines',
    loadComponent: () =>
      import('./features/domains/pipelines/pipelines.component').then(
        (m) => m.PipelinesComponent,
      ),
    title: 'Pipelines · Code Atlas Explorer',
  },
  {
    path: 'federation',
    loadComponent: () =>
      import('./features/domains/federation/federation.component').then(
        (m) => m.FederationComponent,
      ),
    title: 'Federação · Code Atlas Explorer',
  },
  {
    path: 'diagnostics',
    loadComponent: () =>
      import('./features/diagnostics/diagnostics.component').then(
        (m) => m.DiagnosticsComponent,
      ),
    title: 'Diagnósticos · Code Atlas Explorer',
  },
  {
    path: 'settings',
    loadComponent: () =>
      import('./features/settings/settings.component').then((m) => m.SettingsComponent),
    title: 'Configurações · Code Atlas Explorer',
  },
  { path: '**', redirectTo: 'dashboard' },
];
