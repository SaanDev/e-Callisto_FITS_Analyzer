//! On Windows: the executable's icon and version information
//! (`assets/windows/callisto-lite.rc`), as Explorer and the installer show them.

fn main() {
    println!("cargo:rerun-if-changed=assets/windows/callisto-lite.rc");
    println!("cargo:rerun-if-changed=../../../assets/branding/icon.ico");
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() != Ok("windows") {
        return;
    }
    let version = std::env::var("CARGO_PKG_VERSION").unwrap_or_default();
    let mut parts: Vec<u32> = version.split(['.', '-']).filter_map(|p| p.parse().ok()).collect();
    parts.resize(4, 0);
    let numeric = parts.iter().map(u32::to_string).collect::<Vec<_>>().join(",");
    let icon =
        std::path::Path::new(&std::env::var("CARGO_MANIFEST_DIR").unwrap()).join("../../../assets/branding/icon.ico");
    // rc takes forward slashes, which need no escaping in its string.
    let icon = icon.to_string_lossy().replace('\\', "/");
    embed_resource::compile(
        "assets/windows/callisto-lite.rc",
        [format!("VERSION_NUMERIC={numeric}"), format!("VERSION_TEXT=\"{version}\""), format!("ICON_PATH=\"{icon}\"")],
    )
    .manifest_optional()
    .unwrap();
}
