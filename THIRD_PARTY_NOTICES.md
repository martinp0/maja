# Third-party notices

The MIT license in [LICENSE](LICENSE) covers the source code in this repository. It does not
cover third-party material that keeps its own terms:

- OpenStreetMap data, which is ODbL. This repository does not redistribute map
  tiles. The Mája PWA loads them at runtime from the public OSM tile server
  and must keep the copyright attribution.
- Dependencies (MIT, BSD, Apache-2.0, MPL-2.0, and others). MPL-2.0 applies
  only to those library files, which are not vendored here.
- The PostGIS database images (imresamu/postgis). PostGIS is GPL-2.0-or-later
  and runs as a separate program. This application talks to it over SQL.
