# Changelog

## [0.9.0](https://github.com/pyvvo/funcd-python/compare/v0.8.2...v0.9.0) (2026-10-12)


### Features

* **shim:** context.sql, SQL over the local API (funcd ADR-0233) ([#82](https://github.com/pyvvo/funcd-python/issues/82)) ([170a6d5](https://github.com/pyvvo/funcd-python/commit/170a6d5a89b16fafa0af70b80860e6fe7b3ba456))

## [0.8.2](https://github.com/pyvvo/funcd-python/compare/v0.8.1...v0.8.2) (2026-10-12)


### Bug Fixes

* **examples:** catalog-quack's Quack client follows funcd's engine to duckdb 1.5.6 ([#80](https://github.com/pyvvo/funcd-python/issues/80)) ([ad9e0c9](https://github.com/pyvvo/funcd-python/commit/ad9e0c9213dada0446ae25e65c72ae322a191db4))

## [0.8.1](https://github.com/pyvvo/funcd-python/compare/v0.8.0...v0.8.1) (2026-10-11)


### Bug Fixes

* **bundle:** pin the base image by digest and check the DuckDB extensions ([#77](https://github.com/pyvvo/funcd-python/issues/77)) ([095ba8a](https://github.com/pyvvo/funcd-python/commit/095ba8a9d556766227b46a7588b09d179d3e0ba3))

## [0.8.0](https://github.com/pyvvo/funcd-python/compare/v0.7.0...v0.8.0) (2026-10-11)


### Features

* **shim:** the handler reads its deadline and a late call is stopped (funcd ADR-0226) ([#74](https://github.com/pyvvo/funcd-python/issues/74)) ([7f0acbf](https://github.com/pyvvo/funcd-python/commit/7f0acbfd8bdfcc2f6dd694a009e6cd6626673c79))

## [0.7.0](https://github.com/pyvvo/funcd-python/compare/v0.6.0...v0.7.0) (2026-10-10)


### Features

* **shim:** readiness checks the function's declared dependencies (funcd ADR-0215) ([#72](https://github.com/pyvvo/funcd-python/issues/72)) ([6034cc1](https://github.com/pyvvo/funcd-python/commit/6034cc19bd13b72139ebe3fcaa8e5e92ad7f462f))

## [0.6.0](https://github.com/pyvvo/funcd-python/compare/v0.5.1...v0.6.0) (2026-10-07)


### ⚠ BREAKING CHANGES

* **shim:** signed_url takes expiry as a duration string ([#69](https://github.com/pyvvo/funcd-python/issues/69))

### Features

* **shim:** signed_url takes expiry as a duration string ([#69](https://github.com/pyvvo/funcd-python/issues/69)) ([eb19b99](https://github.com/pyvvo/funcd-python/commit/eb19b99f06569e65563818b058f63c5891568541))

## [0.5.1](https://github.com/pyvvo/funcd-python/compare/v0.5.0...v0.5.1) (2026-10-05)


### Bug Fixes

* **examples:** accept big in the log-burst input contract ([#67](https://github.com/pyvvo/funcd-python/issues/67)) ([d7171c1](https://github.com/pyvvo/funcd-python/commit/d7171c1acdb4bddd91bf15d6834e6362156316f6))

## [0.5.0](https://github.com/pyvvo/funcd-python/compare/v0.4.0...v0.5.0) (2026-10-05)


### Features

* **shim:** bound each log record and line-buffer stdout ([#64](https://github.com/pyvvo/funcd-python/issues/64)) ([be72eab](https://github.com/pyvvo/funcd-python/commit/be72eabbefddfda72568fc70a6d4ff497095a84e))
* **shim:** send traceparent and emit a CLIENT span from context.invoke ([#66](https://github.com/pyvvo/funcd-python/issues/66)) ([cd5e1e6](https://github.com/pyvvo/funcd-python/commit/cd5e1e614f55bdacc9f160be375700b60a57219c))

## [0.4.0](https://github.com/pyvvo/funcd-python/compare/v0.3.6...v0.4.0) (2026-10-05)


### Features

* **shim:** load pool members on their own and name the member on every channel ([#62](https://github.com/pyvvo/funcd-python/issues/62)) ([cef420a](https://github.com/pyvvo/funcd-python/commit/cef420abe8bcf8af98c6aaa06ec7f7e393146d37))

## [0.3.6](https://github.com/pyvvo/funcd-python/compare/v0.3.5...v0.3.6) (2026-10-05)


### Bug Fixes

* **shim:** bound the int64 format to the JSON safe-integer range ([#58](https://github.com/pyvvo/funcd-python/issues/58)) ([b172a72](https://github.com/pyvvo/funcd-python/commit/b172a724cf3b7cae2113f64438097bdb9b6f8f3d))

## [0.3.5](https://github.com/pyvvo/funcd-python/compare/v0.3.4...v0.3.5) (2026-10-03)


### Bug Fixes

* **shim:** keep identical silver rows, name hello-world handler ([#56](https://github.com/pyvvo/funcd-python/issues/56)) ([3c7d184](https://github.com/pyvvo/funcd-python/commit/3c7d1847c07d127548d392d88a12b7f532ca7d43))

## [0.3.4](https://github.com/pyvvo/funcd-python/compare/v0.3.3...v0.3.4) (2026-10-03)


### Bug Fixes

* **shim:** harden pool, bundler and examples (14 reviewed fixes) ([#52](https://github.com/pyvvo/funcd-python/issues/52)) ([d436641](https://github.com/pyvvo/funcd-python/commit/d436641505835995437eb2d095a61094d65e9433))

## [0.3.3](https://github.com/pyvvo/funcd-python/compare/v0.3.2...v0.3.3) (2026-10-03)


### Bug Fixes

* **shim:** match Node wire bytes, isolate pool members, fix examples ([#36](https://github.com/pyvvo/funcd-python/issues/36)) ([709249d](https://github.com/pyvvo/funcd-python/commit/709249de74e6c35ffc155669614c65539099d135))

## [0.3.2](https://github.com/pyvvo/funcd-python/compare/v0.3.1...v0.3.2) (2026-10-02)


### Bug Fixes

* **shim:** harden replies, logging, pool isolation and async handlers ([#19](https://github.com/pyvvo/funcd-python/issues/19)) ([90e0d47](https://github.com/pyvvo/funcd-python/commit/90e0d47a965897afd3ac0fb623454a519d85d812))

## [0.3.1](https://github.com/pyvvo/funcd-python/compare/v0.3.0...v0.3.1) (2026-10-02)


### Bug Fixes

* **bundle:** exit 2 on a usage error and check the vendored tree as built ([#14](https://github.com/pyvvo/funcd-python/issues/14)) ([ff8a410](https://github.com/pyvvo/funcd-python/commit/ff8a410bf6699d7704d7fccee6c97b8f80d20120))

## [0.3.0](https://github.com/pyvvo/funcd-python/compare/v0.2.0...v0.3.0) (2026-10-02)


### Features

* add funcd-bundle and build the examples with it ([#12](https://github.com/pyvvo/funcd-python/issues/12)) ([e553def](https://github.com/pyvvo/funcd-python/commit/e553def39c28eb24b7390ba1aab4ab7d17a563c6))

## [0.2.0](https://github.com/pyvvo/funcd-python/compare/v0.1.0...v0.2.0) (2026-09-27)


### Features

* **shim:** publish funcd-shim to PyPI ([#8](https://github.com/pyvvo/funcd-python/issues/8)) ([7a8b4f4](https://github.com/pyvvo/funcd-python/commit/7a8b4f4681a46a4b67584af064e0cb7fe634d8f2))
