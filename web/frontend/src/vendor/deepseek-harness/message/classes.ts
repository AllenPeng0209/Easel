/** Dependency-free class-name bridge for the vendored MIT components. */
export function cx(...values: (string | undefined | false | null)[]): string {
  return values.filter(Boolean).join(' ');
}
