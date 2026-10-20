# Contributing

Thanks for looking. Mája is a personal self-hosted tracker. Small, focused
changes are welcome.

1. Open an issue or a pull request against `master`. Describe the behavior
   you want to change.
2. Do not commit secrets, `.env` files, tracker fixtures, or real coordinates.
   See [SECURITY.md](SECURITY.md).
3. Keep production compose files compatible. `docker-compose.yml` is what the
   Raspberry Pi deploy uses, including an optional local
   `additional_contexts: webdist` override. Do not rename services or that
   build hook.
4. Run the tests you touched:

   ```bash
   python -m unittest discover -s tests -v
   cd web && npm ci && npm run typecheck
   ```

5. Match the style of the file you edit. Do not reformat unrelated code.

By submitting a contribution you agree it can be included under the MIT
license in [LICENSE](LICENSE).
