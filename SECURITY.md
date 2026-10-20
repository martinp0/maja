# Security

Mája stores a dog's precise location, which is also a home address, plus the
password of a Tractive account. Treat that as private data.

## Reporting a vulnerability

Please do not open a public GitHub issue for a security problem.

Once this repository is public, use **GitHub private vulnerability reporting**
(Security → Report a vulnerability). Until then, contact the maintainer
directly and wait for a reply before disclosing details.

Include what you found, how to reproduce it, and the version or commit. Do
not include live credentials, `.env` files, location history, or home
coordinates in the report. Describe them instead (for example "a Tailscale
address in README.md").

## What not to commit

- `.env` and any real value for `TRACTIVE_PASSWORD`, `APP_PIN`,
  `APP_SESSION_SECRET`, `VAPID_PRIVATE_KEY`, or database passwords
- `fixtures/*.jsonl` (raw tracker payloads include home coordinates)
- `HOME_LAT` / `HOME_LON`
- Private hostnames, Tailscale `100.x` addresses, and home GPS samples

Placeholders belong in `.env.example` only.
