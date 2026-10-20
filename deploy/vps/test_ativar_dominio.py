"""Testes offline dos blocos Caddy para a troca do subdominio."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ATIVAR_DOMINIO_DEFINITIVO as change
import publicar_web_original as current


class DomainSwitchTests(unittest.TestCase):
    def setUp(self):
        self.old = (
            "gfarm.exemplo.com {\n"
            "    reverse_proxy gfarm:8000\n"
            "}\n\n"
            + current.START + "\n"
            + current.domain_name.__name__ + " disabled\n" # fora do bloco nao e dominio real
            + change.OLD_DOMAIN + " {\n"
            "    basic_auth {\n"
            "        contas $2a$12$" + "a" * 53 + "\n"
            "    }\n"
            "    reverse_proxy contas3-backend:8767\n"
            "}\n"
            + current.END + "\n\n"
            "pluv.exemplo.com {\n"
            "    reverse_proxy pluv:8080\n"
            "}\n"
        )

    def test_preserve_other_sites(self):
        before, after, block = change.caddy_managed_block(self.old)
        replacement = change.replace_block(
            before, after, change.protected_site(change.DOMAIN, "$2a$12$" + "x" * 53)
        )
        self.assertIn("gfarm.exemplo.com", replacement)
        self.assertIn("pluv.exemplo.com", replacement)
        self.assertIn(change.DOMAIN, replacement)
        self.assertEqual(replacement.count(current.START), 1)
        self.assertEqual(replacement.count(current.END), 1)

    def test_reject_conflicting_domain_outside_own_block(self):
        self.assertRaises(
            RuntimeError,
            change.caddy_managed_block,
            self.old + "\n" + change.DOMAIN + " { reverse_proxy outro:8000 }\n",
        )

    def test_refuse_caddyfile_without_managed_block(self):
        self.assertRaises(RuntimeError, change.caddy_managed_block, "example.com {}")

    def test_redirect_points_only_to_new_origin(self):
        redirect = change.OLD_DOMAIN + " {\n" + "redir https://" + change.DOMAIN + "{uri} 302\n}"
        self.assertIn("https://" + change.DOMAIN + "{uri} 302", redirect)


if __name__ == "__main__":
    unittest.main()
