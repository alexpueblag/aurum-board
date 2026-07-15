/**
 * Métricas YOD (aurum-board) · Autorización con el Portero YOD
 *
 * REAPERTURA SEGURA — el deployment anterior quedó archivado en la
 * contención 2026-07-12 porque el HTML público contenía el secreto
 * compartido. El nuevo modelo: el front manda `k` (credencial del
 * Portero: liga mágica de 90 días, clave de equipo o Google) y este
 * backend la valida del lado del servidor. Sin credencial válida
 * responde { ok:false, error:'liga' } y no entrega ni un dato.
 *
 * Cómo conectar (una vez, en el Apps Script del CRM - YOD (cuestionario)):
 *   1. Pega este archivo completo como un archivo .gs más del proyecto.
 *   2. En doGet (recurso=board) y doPost del board, exige la credencial
 *      ANTES de servir o escribir datos:
 *
 *        // doGet: var k = (e.parameter && e.parameter.k) || '';
 *        // doPost: var k = payload.k || '';
 *        if (!credencialValida_(k)) {
 *          return jsonOut_({ ok: false, error: 'liga' });
 *        }
 *
 *      Ojo: las rutas PÚBLICAS del cuestionario (guardar lead) siguen
 *      abiertas; la credencial se exige solo en las rutas del board.
 *      GASTO usa la misma credencial del Portero; no existe una segunda
 *      clave de escritura en el HTML.
 *
 *   3. Implementar → Nueva implementación (el deployment viejo quedó
 *      archivado, por eso aquí SÍ es implementación nueva) ·
 *      Ejecutar como: yo · Acceso: cualquier persona.
 *   4. Copia la URL /exec y pégala en CONFIG.SHEET_URL de index.html.
 *   5. En Control Maestro, marca SYS-MARKETING como Activo.
 */

// Endpoint del Portero YOD (potenciales-yod) — valida ligas, claves y sesiones.
const PORTERO_EXEC = 'https://script.google.com/macros/s/AKfycbwlDDCWWzOWYZsUpBU9uqsQ7aenQ469PF6s6FkNlBFS1_cJSU5njG9oQmuyELy5zlqzFg/exec';
const AUTH_TTL_OK  = 600;  // 10 min de caché para credenciales válidas
const AUTH_TTL_BAD = 60;   // 1 min para rechazadas (reintentos rápidos tras dar de alta)

/**
 * Valida la credencial contra el Portero (server-to-server), con caché
 * por hash para no golpear al Portero en cada request. Fail-closed.
 */
function credencialValida_(k) {
  k = String(k || '').trim();
  if (k.length < 4) return false;

  const cache = CacheService.getScriptCache();
  const ck = 'auth_' + Utilities.base64EncodeWebSafe(
    Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, k)).slice(0, 24);
  const hit = cache.get(ck);
  if (hit) return hit === '1';

  let ok = false;
  try {
    // Canjeamos SIN &board=: solo preguntamos "¿es una sesión viva?" y dejamos que el
    // Portero devuelva rol + boards. La autorización a MK la decide ESTE backend abajo.
    // (Pasar &board=MK hacía que el Portero respondiera {ok:false,error:'board'} para
    //  colaboradores con acceso restringido, y el backend lo traducía a 'liga' → lockout.)
    const r = UrlFetchApp.fetch(PORTERO_EXEC + '?recurso=canje&t=' + encodeURIComponent(k),
      { muteHttpExceptions: true, followRedirects: true });
    const j = JSON.parse(r.getContentText());
    const role = String(j && j.rol || '').toLowerCase();
    const boards = String(j && j.boards || '');
    ok = !!(j && j.ok && (role === 'admin' || boards.trim() === '*' ||
      boards.split(',').map(function (v) { return v.trim().toUpperCase(); }).indexOf('MK') >= 0));
  } catch (err) {
    ok = false;  // Portero inaccesible → fail-closed
  }
  cache.put(ck, ok ? '1' : '0', ok ? AUTH_TTL_OK : AUTH_TTL_BAD);
  return ok;
}
