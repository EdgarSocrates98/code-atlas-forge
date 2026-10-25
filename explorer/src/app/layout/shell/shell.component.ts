import { ChangeDetectionStrategy, Component } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { ThemeService } from '../../core/state/theme.service';
import { RepositoryContextService } from '../../core/state/repository.service';

interface NavItem {
  path: string;
  label: string;
}

const NAV_ITEMS: NavItem[] = [
  { path: '/dashboard', label: 'Painel' },
  { path: '/repositories', label: 'Repositórios' },
  { path: '/graph', label: 'Grafo' },
  { path: '/search', label: 'Busca' },
  { path: '/impact', label: 'Impacto' },
  { path: '/revisions', label: 'Revisões' },
  { path: '/context', label: 'Context Packs' },
  { path: '/diagnostics', label: 'Diagnósticos' },
  { path: '/settings', label: 'Configurações' },
];

@Component({
  selector: 'atlas-shell',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink, RouterLinkActive, RouterOutlet],
  templateUrl: './shell.component.html',
  styleUrl: './shell.component.css',
})
export class ShellComponent {
  readonly navItems = NAV_ITEMS;

  constructor(
    readonly theme: ThemeService,
    readonly repositories: RepositoryContextService,
  ) {}

  toggleTheme(): void {
    this.theme.toggle();
  }
}
