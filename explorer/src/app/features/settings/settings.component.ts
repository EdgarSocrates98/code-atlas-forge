import { ChangeDetectionStrategy, Component } from '@angular/core';
import { ThemeService, ThemePreference } from '../../core/state/theme.service';

@Component({
  selector: 'atlas-settings',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="settings">
      <h1>Configurações</h1>

      <fieldset class="settings__group">
        <legend>Tema</legend>
        <label>
          <input
            type="radio"
            name="theme"
            value="system"
            [checked]="theme.preference() === 'system'"
            (change)="setTheme('system')"
          />
          Seguir o sistema
        </label>
        <label>
          <input
            type="radio"
            name="theme"
            value="light"
            [checked]="theme.preference() === 'light'"
            (change)="setTheme('light')"
          />
          Claro
        </label>
        <label>
          <input
            type="radio"
            name="theme"
            value="dark"
            [checked]="theme.preference() === 'dark'"
            (change)="setTheme('dark')"
          />
          Escuro
        </label>
      </fieldset>

      <p class="settings__note">
        Code Atlas Explorer é local-first: nenhum dado é enviado para servidores externos e nenhuma
        telemetria é coletada.
      </p>
    </section>
  `,
  styles: [
    `
      .settings {
        padding: var(--atlas-space-5);
        max-width: 60ch;
      }

      .settings__group {
        display: flex;
        flex-direction: column;
        gap: var(--atlas-space-2);
        border: 1px solid var(--atlas-color-border);
        border-radius: var(--atlas-radius-md);
        padding: var(--atlas-space-4);
        margin: var(--atlas-space-4) 0;
      }

      .settings__note {
        color: var(--atlas-color-text-muted);
        font-size: var(--atlas-font-size-sm);
      }
    `,
  ],
})
export class SettingsComponent {
  constructor(readonly theme: ThemeService) {}

  setTheme(preference: ThemePreference): void {
    this.theme.setPreference(preference);
  }
}
