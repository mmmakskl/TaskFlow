# Repository Guidelines

## Project Structure & Module Organization

This repository currently has no application source, tests, or assets. Keep implementation code in clearly named directories such as `src/`, automated tests in `tests/` (or beside modules when the chosen framework expects that), and static files in `assets/`. Add a short README when introducing the application so contributors can find its purpose and setup instructions.

## Build, Test, and Development Commands

No build system or development commands are configured yet. When adding one, expose the common workflows through the project’s standard tool (for example, `npm run dev`, `npm test`, or `make build`) and document prerequisites and expected output in the README. Run the relevant build, tests, and lint or format checks before submitting changes.

## Coding Style & Naming Conventions

Follow the conventions of the language and framework selected for the project. Use consistent indentation, descriptive names, and small modules with clear responsibilities. Prefer `PascalCase` for types and components, `camelCase` for functions and variables, and lowercase, hyphen-separated names for directories and general file names unless the ecosystem specifies otherwise. Add formatter and linter configuration alongside the first implementation rather than relying on undocumented local settings.

## Testing Guidelines

There is no test framework or coverage target configured yet. Add tests with new behavior and bug fixes, use the framework’s conventional test location and naming pattern (for example, `tests/example.test.ts`), and document how to run them. Keep tests deterministic and focused on observable behavior.

## Commit & Pull Request Guidelines

No Git history is available here to establish existing commit conventions. Write concise commit subjects in the imperative mood (for example, `Add task filtering`). Pull requests should describe the change and its motivation, list verification commands and results, and link related issues. Include screenshots for user interface changes when applicable.

## Security & Configuration

Keep credentials and machine-specific settings out of version control. Provide safe examples in an environment template (such as `.env.example`) and document required configuration without including real secrets.
