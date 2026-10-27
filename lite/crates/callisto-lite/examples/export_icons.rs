//! Renders the application icon at the hicolor theme's standard sizes for the
//! Linux package (`lite/packaging/linux/build_deb_linux.sh`), so the build
//! needs no ImageMagick:
//! `cargo run --release -p callisto-lite --example export_icons -- <source.png> <hicolor dir> <icon name>`.
//!
//! Desktops only look in the size folders hicolor's `index.theme` lists, which
//! stop at 512×512: an icon anywhere else shows as the generic gear.

use image::imageops::{self, FilterType};

const SIZES: [u32; 8] = [16, 24, 32, 48, 64, 128, 256, 512];

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let [source, hicolor, name] = args.as_slice() else {
        return Err("usage: export_icons <source.png> <hicolor dir> <icon name>".into());
    };
    let icon = image::open(source)?.into_rgba8();
    for size in SIZES {
        let dir = std::path::Path::new(hicolor).join(format!("{size}x{size}/apps"));
        std::fs::create_dir_all(&dir)?;
        let path = dir.join(format!("{name}.png"));
        imageops::resize(&icon, size, size, FilterType::Lanczos3).save(&path)?;
        println!("{}", path.display());
    }
    Ok(())
}
