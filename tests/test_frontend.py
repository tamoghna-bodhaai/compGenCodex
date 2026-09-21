from pathlib import Path
import json
import unittest


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"


class FrontendContractTests(unittest.TestCase):
    def test_next_typescript_application_is_configured(self) -> None:
        package = json.loads((FRONTEND / "package.json").read_text())
        self.assertIn("next", package["dependencies"])
        self.assertIn("react", package["dependencies"])
        self.assertEqual(package["scripts"]["typecheck"], "tsc --noEmit")
        self.assertTrue((FRONTEND / "next.config.ts").exists())
        self.assertTrue((FRONTEND / "tsconfig.json").exists())

    def test_app_router_exposes_every_workspace_route(self) -> None:
        for route in (
            "src/app/page.tsx",
            "src/app/login/page.tsx",
            "src/app/question-bank/page.tsx",
            "src/app/new-paper/page.tsx",
            "src/app/papers/[paperId]/page.tsx",
        ):
            self.assertTrue((FRONTEND / route).exists(), route)

    def test_next_proxies_api_requests_to_fastapi(self) -> None:
        config = (FRONTEND / "next.config.ts").read_text()
        self.assertIn('source: "/api/:path*"', config)
        self.assertIn("BACKEND_URL", config)
        api = (FRONTEND / "src/lib/api.ts").read_text()
        self.assertIn("fetch(`/api", api)

    def test_test_access_login_and_route_guard_are_present(self) -> None:
        login = (FRONTEND / "src/components/login-screen.tsx").read_text()
        proxy = (FRONTEND / "src/proxy.ts").read_text()
        api = (FRONTEND / "src/lib/api.ts").read_text()
        self.assertIn("utils@bodhaai.tech", login)
        self.assertIn("/auth/login", api)
        self.assertIn("paper_studio_session", proxy)

    def test_fastapi_no_longer_mounts_legacy_static_frontend(self) -> None:
        main = (ROOT / "backend/app/main.py").read_text()
        self.assertNotIn("StaticFiles", main)
        self.assertNotIn('app.mount("/"', main)
        self.assertFalse((FRONTEND / "app.js").exists())
        self.assertFalse((FRONTEND / "index.html").exists())

    def test_critical_workflows_remain_wired(self) -> None:
        source = "\n".join(path.read_text() for path in (FRONTEND / "src/components").glob("*.tsx"))
        for endpoint in (
            "/generation/pause",
            "/generation/resume",
            "/generation/cancel",
            "/regenerate-selected",
            "/regenerate-unlocked",
            "/solutions/generate",
            "/questions/manual",
        ):
            self.assertIn(endpoint, source)
        self.assertIn("downloadPaper", source)
        self.assertIn("custom_instruction", source)
        self.assertIn("View seeds & compare", source)
        self.assertIn("Compare paper", source)
        self.assertIn("Compare with original", source)
        self.assertIn("Surface wording overlap", source)
        self.assertIn("View partial paper", source)
        self.assertIn("Completed questions are ready to review.", source)
        self.assertIn("/questions/${questionId}/seeds", (FRONTEND / "src/lib/api.ts").read_text())
        self.assertIn("/papers/${paperId}/comparison", (FRONTEND / "src/lib/api.ts").read_text())

    def test_accessibility_and_responsive_contracts_are_present(self) -> None:
        styles = (FRONTEND / "src/styles/ui.module.css").read_text()
        globals_css = (FRONTEND / "src/app/globals.css").read_text()
        self.assertIn("@media (max-width: 950px)", styles)
        self.assertIn("@media (max-width: 560px)", styles)
        self.assertIn("@media (prefers-reduced-motion: reduce)", globals_css)
        self.assertIn(":focus-visible", globals_css)
        self.assertIn("-apple-system", globals_css)


if __name__ == "__main__":
    unittest.main()
