import { ChangeDetectionStrategy, Component } from '@angular/core';
import { ShellComponent } from './layout/shell/shell.component';

@Component({
    selector: 'atlas-root',
    changeDetection: ChangeDetectionStrategy.OnPush,
    imports: [ShellComponent],
    template: `<atlas-shell />`
})
export class AppComponent {}
