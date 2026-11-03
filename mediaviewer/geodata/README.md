# Offline place names

Derived from [Natural Earth](https://www.naturalearthdata.com/), retrieved 2026-11-04:

- `ne_50m_admin_0_countries`: country polygons and Russian names.
- `ne_10m_populated_places`: city positions, names, country codes and population.

Source: [natural-earth-vector GeoJSON](https://github.com/nvkelso/natural-earth-vector/tree/master/geojson).
Natural Earth data is [public domain](https://www.naturalearthdata.com/about/terms-of-use/).
`tools/build_places.py` keeps only the fields used by the application.

This is a coarse gazetteer for suggested album names, not an address or hotel lookup.
Country borders follow the source dataset. Coordinates are processed locally;
the application never sends photo coordinates to a geocoding service.
