# Librerías incluidas

Se sirven desde el dashboard en `/vendor/<archivo>` (lista blanca `VENDOR` en `dashboard.py`), así el grafo y el chat
funcionan sin internet y no dependen de un CDN. Bajadas del registro de npm y verificadas con el `integrity` que
publica npm.

| archivo | paquete | versión | licencia |
|---|---|---|---|
| `d3.min.js` | d3 | 7.9.0 | ISC (`d3.LICENSE.txt`) |
| `marked.min.js` | marked | 12.0.2 | MIT (`marked.LICENSE.txt`) |
| `purify.min.js` | dompurify | 3.5.0 | Apache-2.0 o MPL-2.0 (`dompurify.LICENSE.txt`) |

Para actualizar una: bajar el tarball de `https://registry.npmjs.org/<paquete>/<versión>`, comprobar `dist.integrity`,
copiar el `.min.js` y la licencia, y cambiar la versión acá y en CHANGES.md.
