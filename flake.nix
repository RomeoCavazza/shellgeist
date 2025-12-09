{
  description = "AI dev env (LangChain + LangGraph branchés sur Ollama)";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.05";
  };

  outputs = { nixpkgs, ... }:
  let
    system = "x86_64-linux";
    pkgs = import nixpkgs {
      inherit system;
      config.allowUnfree = true;
    };

    pythonEnv = pkgs.python311.withPackages (ps: [ ps.pip ]);
  in {
    devShells.${system}.default = pkgs.mkShell {
      packages = [
        pythonEnv
        pkgs.git
        pkgs.stdenv.cc.cc.lib  # libstdc++.so.6
      ];

      shellHook = ''
        # Rendre libstdc++ visible pour les roues Python (tokenizers, etc.)
        export LD_LIBRARY_PATH="${pkgs.stdenv.cc.cc.lib}/lib:$LD_LIBRARY_PATH"

        echo "[ai-lab] Activation du venv Python (.venv)..."

        if [ ! -d .venv ]; then
          python -m venv .venv
          source .venv/bin/activate
          pip install --upgrade pip
          pip install "openai>=1.0.0" "langchain" "langgraph" "aider-chat" "textual" "psutil"
        else
          source .venv/bin/activate
        fi

        echo "[ai-lab] Env prêt. OPENAI_BASE_URL=$OPENAI_BASE_URL"
      '';
    };
  };
}
