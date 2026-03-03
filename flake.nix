{
  description = "ShellGeist – AI-powered code editing assistant for Neovim";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};

        python = pkgs.python313;

        pythonEnv = python.withPackages (ps: with ps; [
          pydantic
          anyio
          # dev
          pytest
          pytest-asyncio
          ruff
          mypy
        ]);

        shellgeist = python.pkgs.buildPythonApplication {
          pname = "shellgeist";
          version = "0.1.0";
          pyproject = true;

          src = ./.;

          build-system = [ python.pkgs.setuptools python.pkgs.wheel ];

          dependencies = with python.pkgs; [
            pydantic
            anyio
          ];

          meta = {
            homepage = "https://github.com/RomeoCavazza/shellgeist";
            description = "AI-powered code editing assistant for Neovim";
            license = pkgs.lib.licenses.mit;
            mainProgram = "shellgeist";
          };
        };
      in
      {
        packages = {
          default = shellgeist;
          inherit shellgeist;
        };

        apps.default = flake-utils.lib.mkApp { drv = shellgeist; };

        devShells.default = pkgs.mkShell {
          packages = [
            pythonEnv
            pkgs.nixd      # nix LSP
          ];
          env.PYTHONPATH = "${toString ./backend}";
          shellHook = ''
            echo "🐚 ShellGeist dev shell (Python ${python.version})"
          '';
        };
      }
    );
}