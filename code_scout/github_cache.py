"""
GitHub Repository Cache for containerized deployments.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional


class GitHubRepositoryCache:
    """
    Cache GitHub repositories in container lifecycle.
    """

    def __init__(
        self,
        cache_dir: str = "/tmp/github_cache",
        max_age_hours: int = 24,
        max_cache_size_mb: int = 5000,
    ):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.max_age = timedelta(hours=max_age_hours)
        self.max_cache_size_mb = max_cache_size_mb
        self.lock = threading.Lock()
        self._cache_metadata: Dict[str, dict] = {}

    def _get_cache_key(self, repo_url: str, ref: Optional[str] = None) -> str:
        key_string = f"{repo_url}:{ref or 'default'}"
        return hashlib.md5(key_string.encode()).hexdigest()

    def _get_cache_path(self, cache_key: str) -> Path:
        return self.cache_dir / cache_key

    def _is_cache_valid(self, cache_path: Path) -> bool:
        if not cache_path.exists():
            return False

        mtime = datetime.fromtimestamp(cache_path.stat().st_mtime)
        age = datetime.now() - mtime

        return age < self.max_age

    def _cleanup_old_caches(self) -> None:
        if not self.cache_dir.exists():
            return

        cached_repos = []
        for repo_dir in self.cache_dir.iterdir():
            if repo_dir.is_dir():
                stat = repo_dir.stat()
                cached_repos.append(
                    {
                        "path": repo_dir,
                        "mtime": stat.st_mtime,
                        "size_mb": sum(f.stat().st_size for f in repo_dir.rglob("*") if f.is_file())
                        / (1024 * 1024),
                    }
                )

        cached_repos.sort(key=lambda item: item["mtime"])

        total_size_mb = sum(repo["size_mb"] for repo in cached_repos)

        while total_size_mb > self.max_cache_size_mb and cached_repos:
            oldest = cached_repos.pop(0)
            try:
                shutil.rmtree(oldest["path"])
                total_size_mb -= oldest["size_mb"]
                print(f"Removed old cache: {oldest['path'].name} ({oldest['size_mb']:.1f}MB)")
            except Exception as exc:
                print(f"Failed to remove cache {oldest['path']}: {exc}")

    def get_or_clone(
        self,
        repo_url: str,
        ref: Optional[str] = None,
        github_token: Optional[str] = None,
        shallow: bool = True,
    ) -> Optional[str]:
        cache_key = self._get_cache_key(repo_url, ref)
        cache_path = self._get_cache_path(cache_key)

        with self.lock:
            if self._is_cache_valid(cache_path):
                print(f"Using cached repository: {cache_key}")
                return str(cache_path)

            print(f"Cloning repository to cache: {repo_url}")

            if cache_path.exists():
                shutil.rmtree(cache_path)

            clone_url = repo_url
            if github_token and "github.com" in repo_url:
                clone_url = repo_url.replace("https://", f"https://{github_token}@")

            cmd = ["git", "clone"]

            if shallow:
                cmd.extend(["--depth", "1"])

            if ref:
                cmd.extend(["--branch", ref, "--single-branch"])

            cmd.extend([clone_url, str(cache_path)])

            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=300,
                )

                if result.returncode != 0:
                    print(f"Clone failed: {result.stderr}")
                    return None

                self._cleanup_old_caches()

                return str(cache_path)

            except subprocess.TimeoutExpired:
                print("Clone timeout: repository too large")
                return None
            except Exception as exc:
                print(f"Clone error: {exc}")
                return None

    def clear_cache(self, cache_key: Optional[str] = None) -> None:
        with self.lock:
            if cache_key:
                cache_path = self._get_cache_path(cache_key)
                if cache_path.exists():
                    shutil.rmtree(cache_path)
            else:
                if self.cache_dir.exists():
                    shutil.rmtree(self.cache_dir)
                    self.cache_dir.mkdir(parents=True, exist_ok=True)

    def get_cache_info(self) -> dict:
        if not self.cache_dir.exists():
            return {"total_repos": 0, "total_size_mb": 0, "repos": []}

        repos = []
        total_size = 0

        for repo_dir in self.cache_dir.iterdir():
            if repo_dir.is_dir():
                size = sum(f.stat().st_size for f in repo_dir.rglob("*") if f.is_file())
                size_mb = size / (1024 * 1024)
                total_size += size_mb

                repos.append(
                    {
                        "key": repo_dir.name,
                        "size_mb": round(size_mb, 2),
                        "mtime": datetime.fromtimestamp(repo_dir.stat().st_mtime).isoformat(),
                    }
                )

        return {
            "total_repos": len(repos),
            "total_size_mb": round(total_size, 2),
            "max_size_mb": self.max_cache_size_mb,
            "repos": sorted(repos, key=lambda item: item["mtime"], reverse=True),
        }


def get_github_cache(
    cache_dir: Optional[str] = None,
    use_efs: bool = False,
) -> GitHubRepositoryCache:
    global _global_cache

    if _global_cache is None:
        if cache_dir:
            cache_location = cache_dir
        elif use_efs:
            efs_mount = os.getenv("EFS_MOUNT_PATH", "/mnt/efs")
            cache_location = f"{efs_mount}/github_cache"
        else:
            cache_location = "/tmp/github_cache"

        if use_efs:
            max_cache_size = 50000
            max_age_hours = 168
        else:
            max_cache_size = 5000
            max_age_hours = 24

        _global_cache = GitHubRepositoryCache(
            cache_dir=cache_location,
            max_age_hours=max_age_hours,
            max_cache_size_mb=max_cache_size,
        )

    return _global_cache


_global_cache: Optional[GitHubRepositoryCache] = None
