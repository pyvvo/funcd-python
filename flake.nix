{
  description = "funcd Python shim and examples";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs }: let
    supportedSystems = [ "aarch64-darwin" "x86_64-linux" "aarch64-linux" ];
    forAllSystems = nixpkgs.lib.genAttrs supportedSystems;
  in {
    devShells = forAllSystems (system: let
      pkgs = import nixpkgs { inherit system; };
    in {
      # python 3.14 so the subinterpreter pool-host tests run instead of skipping (ADR-0050)
      default = pkgs.mkShellNoCC ({
        packages = with pkgs; [
          python314
          uv
          go
          just
          git
        ];
        UV_PYTHON_DOWNLOADS = "never";
      } // pkgs.lib.optionalAttrs pkgs.stdenv.isLinux {
        # PyPI's manylinux wheels (duckdb) link libstdc++, which Nix's python cannot find on Linux
        LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath [ pkgs.stdenv.cc.cc.lib ];
      });
    });
  };
}
