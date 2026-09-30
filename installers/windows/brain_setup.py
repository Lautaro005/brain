"""brain-setup.exe: instalador de brain para Windows (se arma con PyInstaller en el workflow de release).

No trae brain adentro: trae install.ps1 (el mismo del comando `irm … | iex`) y lo corre con
PowerShell, así el .exe y el comando instalan exactamente igual y el .exe no hay que regenerarlo en
cada cambio del código. Deja la ventana abierta al final para que se lea el resultado.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def _script() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / "install.ps1"


def main() -> int:
    print("brain: instalando… (se abre una sola vez, tarda un par de minutos)\n")
    with tempfile.TemporaryDirectory() as tmp:
        ps1 = Path(tmp) / "install.ps1"
        ps1.write_bytes(_script().read_bytes())
        code = subprocess.call(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1)],
                               env=os.environ.copy())
    if code == 0:
        print("\nListo. Abrí una terminal nueva y escribí: brain")
    else:
        print(f"\nLa instalación terminó con un error (código {code}). Revisá los mensajes de arriba.")
    if sys.stdin and sys.stdin.isatty():
        input("\nApretá Enter para cerrar.")
    return code


if __name__ == "__main__":
    sys.exit(main())
