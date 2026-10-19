{
  description = "Personal two-way synchronization between DMS Dank Todo and Google Tasks";
  inputs.nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
    in {
      packages = forAllSystems (system:
        let pkgs = import nixpkgs { inherit system; };
        in {
          default = pkgs.callPackage ./nix/package.nix { };
          dank-todo-google-sync = self.packages.${system}.default;
        });
      nixosModules.default = import ./nix/nixos-module.nix;
      checks = forAllSystems (system:
        let pkgs = import nixpkgs { inherit system; };
        in {
          regression = pkgs.runCommand "dms-integration-regression" {
            nativeBuildInputs = [ pkgs.python3 ];
          } ''
            cp ${./google-tasks-sync.py} google-tasks-sync.py
            cp ${./test_google_tasks_sync.py} test_google_tasks_sync.py
            python -B -m unittest discover -v
            touch "$out"
          '';
        });
    };
}
