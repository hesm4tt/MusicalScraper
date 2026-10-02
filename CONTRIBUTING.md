# Contributing

Thanks for helping improve Musical Scraper. Small fixes, clearer documentation, and reports
from different TikTok profiles are all useful.

## Development setup

1. Fork the repository and clone your fork.
2. Run `./setup.sh` to create a local virtual environment and install runtime dependencies.
3. Run `./musical-scraper @username --dry-run` to inspect a profile without downloading files.

To build the desktop package for your current platform, run `./build.sh`. PyInstaller must run
on the same operating system as the package being built. GitHub Actions builds macOS and
Windows packages separately.

## Before opening a pull request

- Keep changes focused and explain the user-visible effect in the pull request description.
- Do not commit downloaded videos, cookies, browser data, profile caches, or personal tokens.
- If a change affects filenames, dates, sorting, or resume behavior, describe a concrete
  example so reviewers can check the intended result.
- Update the README or technical notes when user-facing behavior or setup changes.
- Do not include a creator's video or private account data in an issue or pull request.

## Reporting a problem

Use the bug report form and include your operating system, app version, what you expected, and
the relevant error text. Remove usernames or URLs if you do not want them shared publicly.

By participating, you agree to follow the project's [MIT license](LICENSE) and to be
respectful of creators' rights and privacy.
