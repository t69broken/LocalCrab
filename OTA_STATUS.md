# LocalCrab OTA Update System — Current Status & Issues

## System Components

### Server
- `~/ClaudeLocalClaw/localclaw/` - Python/FastAPI server
- Port: 18798
- Health endpoint: `GET /health`
- Version tracking: `app_version.json`

### Android App
- `~/LocalCrabApp/` - AAR project
- APK: `LocalCrab-app-debug.apk` (served by server)
- Version code stored in `build.gradle`

## Identified Issues

### Issue #1: Duplicate Process Conflict
**Severity:** HIGH  
**Impact:** OTA updates may fail silently due to duplicate process

**Description:**
- Two uvicorn processes can run simultaneously
- Docker container process (root) and bare-metal process (tyson) conflict
- Docker container fails with permission error but leaves zombie process
- Port race condition: whichever wins the race may serve stale code

**Symptoms:**
- `ss -tlnp | grep 18798` shows multiple LISTEN sockets
- Server logs show startup failures but no error to users
- App may connect to wrong process (stale OTA handler)

**Fix:**
- Run `fix_duplicate_processes.sh` before starting
- Or use `start_new.sh` which handles cleanup automatically
- Docker process should be killed before bare-metal starts

### Issue #2: Docker Permission Error
**Severity:** MEDIUM  
**Impact:** Docker container won't start properly

**Description:**
- `/app` volume mount has permission denied error
- Logs show: `PermissionError: [Errno 13] Permission denied: /app`
- Root-owned files not accessible to container user

**Root Cause:**
- `docker-compose.yml` volumes use `/app` mount
- Host directory permissions don't match container user
- Run as root, but app runs as non-root user

**Fix:**
- Run `fix_permissions.sh` to fix ownership
- Or run docker as root user (recommended for development)
- Or configure proper user mapping in docker-compose.yml

### Issue #3: Port 3001 Conflict
**Severity:** LOW  
**Impact:** Some services fail to start

**Description:**
- Port 3001 already in use (likely Vercel dev server)
- Some scripts may fail if trying to use port 3001

**Fix:**
- Identify conflicting process
- Either kill or change port in config

## OTA Update Mechanism

### Flow
1. Android app calls `GET /app/update?current_version_code=N`
2. Server checks `app_version.json`
3. If server version > client, returns update info
4. App downloads APK via `GET /app/download`
5. App triggers package installer with `ACTION_VIEW`

### Version Control
- Server: `~/ClaudeLocalClaw/localclaw/app_version.json`
- Client APK: `~/LocalCrabApp/app/build.gradle`

### Direct Download URLs (for sideload testing)
- `http://10.0.2.158:8111/app-debug.apk` (Python HTTP server)
- `http://10.0.2.158:18798/app/download` (LocalClaw server)
- `http://100.95.211.17:18798/` (Tailscale access)

## Release Checklist

### For OTA Updates
- [ ] Update `~/LocalCrabApp/app/build.gradle` (versionCode, versionName)
- [ ] Build APK: `./gradlew assembleDebug`
- [ ] Copy to server: `cp app/build/outputs/apk/debug/app-debug.apk ~/ClaudeLocalClaw/localclaw/LocalCrab-app-debug.apk`
- [ ] Update `~/ClaudeLocalClaw/localclaw/app_version.json`
- [ ] (Optional) Restart server if needed
- [ ] Verify OTA check works via app or curl
- [ ] Test on device

### For Docker Deployment
- [ ] Fix permissions first
- [ ] Kill any zombie processes
- [ ] Run `docker compose up -d`
- [ ] Verify health check passes
- [ ] Check logs for errors

## Testing Commands

### Process Verification
```bash
# Check port 18798 listeners
ss -tlnp | grep 18798

# Check for zombies
ps -eo pid,state | grep "^Z"

# Health check
curl localhost:18798/health
```

### OTA Check Test
```bash
# Test server-side OTA handler
curl -v localhost:18798/app/update?current_version_code=1 2>&1
```

### Android App OTA
Via app Settings > About > Check for Updates

## Known Workarounds

### If OTA Update Mechanism is Broken
1. Sideload APK directly:
   - Download from `http://SERVER_IP:18798/app/download`
   - Or use `http://SERVER_IP:8111/app-debug.apk`
   - Install manually

2. Fix issue first:
   - Kill zombie: `find /proc -name 'status' -exec grep -l 'uvicorn' {} \; 2>/dev/null | xargs cat /proc/*/status 2>/dev/null | grep 'Name:' | grep uvicorn | awk '{print $1}' | xargs -I {} kill {}`
   - Restart: `pkill -9 uvicorn && ./start_new.sh`

### Zombie Cleanup
```bash
# Find uvicorn processes
ps aux | grep uvicorn

# Kill specific PID
kill -9 <PID>

# Or clean all by port
pkill -9 -f "uvicorn.*18798"
```

## Next Improvements

1. **Add process health check**: Before starting, verify port ownership
2. **Add OTA failure notification**: If update download fails, show error to user
3. **Add version mismatch detection**: Log if client connects to wrong OTA handler version
4. **Add permission monitoring**: Alert if `/app` becomes inaccessible

---
*Generated: 2026-04-19 14:37*
*Auto-updated by LocalCrab Improvement Task*
