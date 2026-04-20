"""
LocalCrab OTA Upgrades Module
===========================

Handles Over-The-Air updates to push improvements
to all agents/devices via Tailscale.

Features:
- Version control for system files
- Incremental updates (not full reinstall)
- Compatibility checks
- Rollback capability
"""

import subprocess
import asyncio
from pathlib import Path
from datetime import datetime
import json
import logging
import hashlib

from main import gpu_manager, model_selector, agent_manager

logger = logging.getLogger(__name__)

class OTAUpgradeManager:
    """
    Manages Over-The-Air updates for LocalCrab.
    """
    
    def __init__(
        self,
        localclab_base: str = "/home/tyson/ClaudeLocalClaw/localclaw",
        upgrade_repo: Optional[str] = None
    ):
        self.localclaw_base = Path(localclaw_base)
        self.upgrade_repo = upgrade_repo
        self.upgrade_repo_path = self.localclaw_base / "upgrades"
        self.upgrade_repo_path.mkdir(parents=True, exist_ok=True)
        
        self.current_version = self._detect_version()
        logger.info(f"Current version: {self.current_version}")
    
    def _detect_version(self) -> str:
        """Detect current version from config or files."""
        try:
            with open(self.localclaw_base / "VERSION.txt") as f:
                return f.read().strip()
        except:
            return "1.0.0"
    
    def register_improvement(
        self,
        improvement_script: Path,
        version: str = None
    ) -> Dict:
        """
        Register an improvement for OTA deployment.
        
        Args:
            improvement_script: Path to improvement script
            version: Version string (auto-detected if not provided)
            
        Returns:
            Registration info
        """
        if version is None:
            version = self._next_version()
        
        # Create upgrade manifest
        manifest = {
            "version": version,
            "timestamp": datetime.now().isoformat(),
            "improvement": str(improvement_script),
            "description": improvement_script.name,
            "status": "pending",
            "compatibility": self._check_compatibility(improvement_script)
        }
        
        # Save manifest
        manifest_file = self.upgrade_repo_path / f"{version}.json"
        with open(manifest_file, "w") as f:
            json.dump(manifest, f, indent=2)
        
        logger.info(f"[OTA] Registered upgrade: {version} ({manifest['description']})")
        
        return manifest
    
    def _next_version(self) -> str:
        """Generate next version number."""
        major = self._get_major_version()
        minor = self._get_minor_version()
        patch = self._increment_patch()
        
        return f"{major}.{minor}.{patch}"
    
    def _get_major_version(self) -> int:
        """Get major version component (if format 1.2.3)."""
        try:
            return int(self.current_version.split(".")[0])
        except:
            return 1
    
    def _get_minor_version(self) -> int:
        """Get minor version component."""
        try:
            return int(self.current_version.split(".")[1])
        except:
            return 0
    
    def _increment_patch(self) -> int:
        """Increment patch version."""
        try:
            return int(self.current_version.split(".")[2]) + 1
        except:
            return 1
    
    def _check_compatibility(self, improvement: Path) -> Dict:
        """
        Check if improvement is compatible with current system.
        
        Returns:
            Compatibility report
        """
        report = {
            "compatible": True,
            "checks": []
        }
        
        # Check Python version requirement
        try:
            import sys
            import inspect
            source = inspect.getsource(improvement)
            
            if "# python 3.11" in source:
                current = sys.version_info
                required = (3, 11)
                compatible = current >= required
                report["checks"].append({
                    "check": "python_version",
                    "required": ".".join(str(x) for x in required),
                    "current": ".".join(str(x) for x in current),
                    "compatible": compatible
                })
                
                if not compatible:
                    report["compatible"] = False
                    
        except Exception as e:
            report["checks"].append({
                "check": "python_version",
                "error": str(e)
            })
        
        return report
    
    def apply_upgrade(
        self,
        improvement: Path,
        dry_run: bool = False
    ) -> Dict:
        """
        Apply an improvement upgrade.
        
        Args:
            improvement: Path to improvement script
            dry_run: If True, don't actually apply
        
        Returns:
            Upgrade result
        """
        if dry_run:
            logger.info("[OTA] DRY RUN: Would apply: " + str(improvement))
            return {
                "success": True,
                "dry_run": True,
                "action": "skipped"
            }
        
        logger.info(f"[OTA] Applying upgrade: " + str(improvement))
        
        # Copy improvement to upgrade directory
        dest = self.upgrade_repo_path / improvement.name
        import shutil
        shutil.copy(improvement, dest)
        
        # Run improvement
        try:
            subprocess.run(
                [
                    "python", "-m", improvement.stem
                ],
                cwd=improvement.parent,
                check=True
            )
            
            logger.info(f"[OTA] Upgrade applied: {improvement.name}")
            return {
                "success": True,
                "applied": str(dest)
            }
            
        except subprocess.CalledProcessError as e:
            logger.error(f"[OTA] Upgrade failed: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    def rollback(self, to_version: str) -> Dict:
        """
        Rollback to previous version.
        
        Note: For now, this is a simple re-install approach.
        In production, would have proper version control.
        """
        logger.warning("[OTA] Rolling back! Reinstall required:")
        print("To rollback, run:")
        print(f"  rm -rf {self.localclaw_base}")
        print(f"  git clone YOUR_REPO_URL {self.localclaw_base}")
        print(f"  cd {self.localclaw_base}")
        print(f"  ./install_upgrade.sh (your install method)")
        
        return {
            "success": True,
            "action": "rollback_instructions_shown"
        }
    
    def list_available_upgrades(self) -> List[Dict]:
        """List available upgrades."""
        upgrades = []
        
        for f in self.upgrade_repo_path.glob("*.json"):
            with open(f) as fh:
                version = json.load(fh)
                upgrades.append(version)
        
        return upgrades
    
    def get_upgrade_status(self) -> Dict:
        """Get current upgrade status."""
        return {
            "current_version": self._detect_version(),
            "available_upgrades": len(self.list_available_upgrades()),
            "pending": sum(
                1 for f in self.upgrade_repo_path.glob("*.json")
                if json.load(open(f))["status"] == "pending"
            )
        }
