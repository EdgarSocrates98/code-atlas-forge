import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { ShellComponent } from './shell.component';

describe('ShellComponent', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ShellComponent],
      providers: [provideRouter([])],
    }).compileComponents();
  });

  it('should render all navigation items', () => {
    const fixture = TestBed.createComponent(ShellComponent);
    fixture.detectChanges();
    const links = (fixture.nativeElement as HTMLElement).querySelectorAll(
      '.shell__sidebar a',
    );
    expect(links.length).toBe(9);
  });

  it('should toggle theme on button click', () => {
    const fixture = TestBed.createComponent(ShellComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance;
    const before = component.theme.resolvedTheme();
    component.toggleTheme();
    expect(component.theme.resolvedTheme()).not.toBe(before);
  });
});
