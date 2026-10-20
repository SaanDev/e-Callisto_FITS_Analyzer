//! `QColorDialog` (`src/widgets/dialogs/qcolordialog.cpp`), as
//! `QColorDialog.getColor` shows it in the full app: the basic and custom
//! colour wells, the hue/saturation field, the value strip, the preview, the
//! HSV/RGB spin boxes and the HTML field.
//!
//! The dialog's layout does not depend on its contents, so the geometry is
//! the one captured from the full app (`dialog_color_*.json`). The field,
//! strip and wells are drawn as Qt draws them at 1.5×: pixmaps made at one
//! pixel per logical pixel and scaled up, `qDrawShadePanel` frames on
//! device pixels, and Fusion's focus rectangle.

use egui::epaint::{Mesh, Vertex};
use egui::{Color32, ColorImage, Id, Pos2, Rect, Sense, Shape, TextureHandle, TextureOptions, Ui, pos2, vec2};

use super::qt;
use super::widgets::mnemonic_text;
use crate::theme::{Palette, darker, lighter};

/// The window's logical size (its `sizeHint`).
pub const SIZE: (f32, f32) = (653.0, 513.0);
const WELL_W: f32 = 28.0;
const WELL_H: f32 = 24.0;
const BASIC_AT: (f32, f32) = (11.0, 33.0);
const CUSTOM_AT: (f32, f32) = (11.0, 344.0);
/// `QColorPicker`: the frame and its 220 × 200 contents.
const PICKER: Rect = Rect { min: Pos2 { x: 247.0, y: 20.0 }, max: Pos2 { x: 469.0, y: 222.0 } };
const PICKER_W: i32 = 220;
const PICKER_H: i32 = 200;
/// `QColorLuminancePicker`.
const LUM: Rect = Rect { min: Pos2 { x: 485.0, y: 17.0 }, max: Pos2 { x: 505.0, y: 225.0 } };
/// `QColorShowLabel`.
const SHOW: Rect = Rect { min: Pos2 { x: 247.0, y: 243.0 }, max: Pos2 { x: 307.0, y: 444.0 } };

/// `QColor::setHsv(h, s, v).rgb()`: Qt's float conversion through 16 bits.
pub fn hsv_to_rgb(h: i32, s: i32, v: i32) -> [u8; 3] {
    let (hue, sat, val) = ((h.clamp(0, 359) * 100) as u32, (s.clamp(0, 255) * 257) as u32, (v.clamp(0, 255) * 257) as u32);
    let div = |x: u32| ((x - (x >> 8) + 0x80) >> 8) as u8;
    if sat == 0 {
        let c = div(val);
        return [c, c, c];
    }
    let hh = if hue == 36000 { 0.0f32 } else { hue as f32 / 6000.0 };
    let ss = sat as f32 / 65535.0;
    let vv = val as f32 / 65535.0;
    let i = hh as i32;
    let f = hh - i as f32;
    let p = vv * (1.0 - ss);
    let q16 = |x: f32| (x * 65535.0).round() as u32;
    let (r, g, b) = if i & 1 == 1 {
        let q = vv * (1.0 - ss * f);
        match i {
            1 => (q, vv, p),
            3 => (p, q, vv),
            _ => (vv, p, q),
        }
    } else {
        let t = vv * (1.0 - ss * (1.0 - f));
        match i {
            0 => (vv, t, p),
            2 => (p, vv, t),
            _ => (t, p, vv),
        }
    };
    [div(q16(r)), div(q16(g)), div(q16(b))]
}

/// `QColor::getHsv`: hue −1 for greys.
pub fn rgb_to_hsv(c: [u8; 3]) -> (i32, i32, i32) {
    let [r, g, b] = c.map(|v| (v as u32 * 257) as f32 / 65535.0);
    let max = r.max(g).max(b);
    let min = r.min(g).min(b);
    let delta = max - min;
    let div = |x: u32| ((x - (x >> 8) + 0x80) >> 8) as i32;
    let value = div((max * 65535.0).round() as u32);
    if delta.abs() <= 1e-12 {
        return (-1, 0, value);
    }
    let sat = div(((delta / max) * 65535.0).round() as u32);
    // qFuzzyCompare against the maximum, which is one of the three.
    let mut hue = if r == max {
        (g - b) / delta
    } else if g == max {
        2.0 + (b - r) / delta
    } else {
        4.0 + (r - g) / delta
    };
    hue *= 60.0;
    if hue < 0.0 {
        hue += 360.0;
    }
    (((hue * 100.0).round() as i32) / 100, sat, value)
}

/// `initRgb`: the 48 basic colours, column by column.
fn basic_colors() -> Vec<[u8; 3]> {
    let mut out = Vec::with_capacity(48);
    for g in 0..4 {
        for r in 0..4 {
            for b in 0..3 {
                out.push([(r * 255 / 3) as u8, (g * 255 / 3) as u8, (b * 255 / 2) as u8]);
            }
        }
    }
    out
}

/// Which control has the keyboard focus (where the focus rectangle shows).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Focus {
    Basic,
    Custom,
    Other,
}

pub struct ColorDialog {
    pub title: String,
    hue: i32,
    sat: i32,
    val: i32,
    rgb: [u8; 3],
    html: String,
    /// The current cell of each well array (`curRow`, `curCol`).
    basic_current: (usize, usize),
    custom_current: (usize, usize),
    custom: [[u8; 3]; 16],
    next_custom: usize,
    focus: Focus,
    picker: Option<TextureHandle>,
    strip: Option<(TextureHandle, (i32, i32))>,
    basic_tex: Option<WellsTexture>,
    custom_tex: Option<WellsTexture>,
}

/// A well array's texture, what it was drawn for, and its device origin.
type WellsTexture = (TextureHandle, (u8, Vec<[u8; 3]>, bool, i32, i32), (i32, i32));

impl ColorDialog {
    pub fn new(title: &str, initial: [u8; 4]) -> Self {
        let rgb = [initial[0], initial[1], initial[2]];
        let (h, s, v) = rgb_to_hsv(rgb);
        Self {
            title: title.into(),
            hue: h.max(0),
            sat: s,
            val: v,
            rgb,
            html: color_html(rgb),
            basic_current: (0, 0),
            custom_current: (0, 0),
            custom: [[255; 3]; 16],
            next_custom: 0,
            focus: Focus::Basic,
            picker: None,
            strip: None,
            basic_tex: None,
            custom_tex: None,
        }
    }

    pub fn color(&self) -> [u8; 4] {
        [self.rgb[0], self.rgb[1], self.rgb[2], 255]
    }

    fn set_hsv(&mut self, h: i32, s: i32, v: i32) {
        (self.hue, self.sat, self.val) = (h.clamp(0, 359), s.clamp(0, 255), v.clamp(0, 255));
        self.rgb = hsv_to_rgb(self.hue, self.sat, self.val);
        self.html = color_html(self.rgb);
    }

    fn set_rgb(&mut self, rgb: [u8; 3]) {
        self.rgb = rgb;
        let (h, s, v) = rgb_to_hsv(rgb);
        // A grey keeps the hue the picker was at.
        if h >= 0 {
            self.hue = h;
        }
        self.sat = s;
        self.val = v;
        self.html = color_html(rgb);
    }

    /// `Some(Some(colour))` on OK, `Some(None)` on Cancel or close.
    pub fn show(&mut self, ctx: &egui::Context, p: &Palette) -> Option<Option<[u8; 4]>> {
        let id = format!("color_dialog:{}", self.title);
        let title = self.title.clone();
        super::dialogs::dialog_window(ctx, &id, &title, vec2(SIZE.0, SIZE.1), p, None, |ui| self.frame(ui, p))
    }

    /// The dialog's contents in `ui` (its whole window).
    pub fn frame(&mut self, ui: &mut Ui, p: &Palette) -> Option<Option<[u8; 4]>> {
        qt::with_app_style(|| self.contents(ui, p))
    }

    fn contents(&mut self, ui: &mut Ui, p: &Palette) -> Option<Option<[u8; 4]>> {
        let o = ui.max_rect().min;
        let at = |x: f32, y: f32, w: f32, h: f32| Rect::from_min_size(o + vec2(x, y), vec2(w, h));
        let ppp = ui.ctx().pixels_per_point();
        let colors = WellColors::new(p);

        mnemonic_text(ui, o.x + 11.0, o.y + 19.0, egui::Align::Min, "&Basic colors", p.text);
        let basic = basic_colors();
        if let Some(i) = self.wells(ui, o, BASIC_AT, 6, &basic, Focus::Basic, &colors, ppp, p) {
            self.set_rgb(basic[i]);
        }
        if qt::button(ui, at(11.0, 183.0, 224.0, 46.0), Id::new("cd_pick"), "", false, true, p).clicked() {
            self.focus = Focus::Other;
        }
        mnemonic_text(ui, o.x + 123.0, o.y + 206.0, egui::Align::Center, "&Pick Screen Color", p.text);
        mnemonic_text(ui, o.x + 11.0, o.y + 330.0, egui::Align::Min, "&Custom colors", p.text);
        let custom = self.custom.to_vec();
        if let Some(i) = self.wells(ui, o, CUSTOM_AT, 2, &custom, Focus::Custom, &colors, ppp, p) {
            self.set_rgb(custom[i]);
        }
        if qt::button(ui, at(11.0, 398.0, 224.0, 46.0), Id::new("cd_add"), "", false, true, p).clicked() {
            self.custom[self.next_custom] = self.rgb;
            self.next_custom = (self.next_custom + 1) % 16;
            self.focus = Focus::Other;
        }
        mnemonic_text(ui, o.x + 123.0, o.y + 421.0, egui::Align::Center, "&Add to Custom Colors", p.text);

        self.picker(ui, o, ppp, p);
        self.luminance(ui, o, ppp, p);
        // QColorShowLabel: a sunken panel filled with the colour.
        let show = SHOW.translate(o.to_vec2());
        shade_panel(ui, show, ppp, p, true);
        let color = Color32::from_rgb(self.rgb[0], self.rgb[1], self.rgb[2]);
        fill_device(ui.painter(), snap_fill(show.shrink(1.0), ppp), color);

        // The spin boxes: hue, saturation, value; red, green, blue.
        let rows = [243.0, 294.0, 345.0];
        let labels = [("Hu&e:", "Hue"), ("&Sat:", "Sat"), ("&Val:", "Val"), ("&Red:", "Red"), ("&Green:", "Green"), ("Bl&ue:", "Blue")];
        let mut hsv = [self.hue, self.sat, self.val];
        let mut rgb = self.rgb.map(i32::from);
        let (mut hsv_changed, mut rgb_changed) = (false, false);
        for (i, (label, key)) in labels.iter().enumerate() {
            let (lx, sx) = if i < 3 { (313.0, 353.0) } else { (477.0, 518.0) };
            let y = rows[i % 3];
            mnemonic_text(ui, o.x + lx + if i < 3 { 34.0 } else { 35.0 }, o.y + y + 22.5, egui::Align::Max, label, p.text);
            let rect = at(sx, y, 118.0, 45.0);
            let max = if i == 0 { 359 } else { 255 };
            if i < 3 {
                if qt::spin_box(ui, rect, Id::new(("cd_spin", *key)), &mut hsv[i], (0, max), "", "", true, p) {
                    hsv_changed = true;
                }
            } else if qt::spin_box(ui, rect, Id::new(("cd_spin", *key)), &mut rgb[i - 3], (0, max), "", "", true, p) {
                rgb_changed = true;
            }
        }
        if hsv_changed {
            self.set_hsv(hsv[0], hsv[1], hsv[2]);
        } else if rgb_changed {
            self.set_rgb(rgb.map(|v| v.clamp(0, 255) as u8));
        }
        mnemonic_text(ui, o.x + 347.0, o.y + 423.0, egui::Align::Max, "&HTML:", p.text);
        let mut html = self.html.clone();
        if qt::line_edit(ui, at(353.0, 402.0, 128.0, 42.0), Id::new("cd_html"), &mut html, "", true, p) {
            if let Some(c) = callisto_core::plot::qt_raster::parse_color(&html) {
                self.set_rgb([c[0], c[1], c[2]]);
            }
            self.html = html;
        }
        if ui.input(|i| i.pointer.any_pressed()) && ui.rect_contains_pointer(at(241.0, 237.0, 401.0, 213.0)) {
            self.focus = Focus::Other;
        }

        let ok = at(529.0, 456.0, 44.0, 46.0);
        let cancel = at(579.0, 456.0, 63.0, 46.0);
        if qt::button(ui, ok, Id::new("cd_ok"), "OK", false, true, p).clicked() {
            return Some(Some(self.color()));
        }
        if qt::button(ui, cancel, Id::new("cd_cancel"), "Cancel", false, true, p).clicked()
            || ui.input(|i| i.key_pressed(egui::Key::Escape))
        {
            return Some(None);
        }
        None
    }

    /// A `QWellArray` of `rows` × 8 cells (column-major colours). Returns
    /// the colour clicked.
    #[allow(clippy::too_many_arguments)]
    fn wells(
        &mut self,
        ui: &Ui,
        o: Pos2,
        at: (f32, f32),
        rows: usize,
        colors: &[[u8; 3]],
        which: Focus,
        wc: &WellColors,
        ppp: f32,
        p: &Palette,
    ) -> Option<usize> {
        let origin = o + vec2(at.0, at.1);
        let key = (which as u8, colors.to_vec(), p.dark, (origin.x * 100.0) as i32, (origin.y * 100.0) as i32);
        let slot = if which == Focus::Basic { &mut self.basic_tex } else { &mut self.custom_tex };
        if slot.as_ref().is_none_or(|(_, k, _)| *k != key) {
            let (img, dev) = wells_image(origin, rows, colors, wc, p.page_bg, ppp);
            let tex = ui.ctx().load_texture(format!("color_wells_{}", which as u8), img, TextureOptions::NEAREST);
            *slot = Some((tex, key, dev));
        }
        if let Some((tex, _, dev)) = slot.as_ref() {
            let size = tex.size_vec2() / ppp;
            let r = Rect::from_min_size(pos2(dev.0 as f32 / ppp, dev.1 as f32 / ppp), size);
            ui.painter().image(tex.id(), r, Rect::from_min_max(pos2(0.0, 0.0), pos2(1.0, 1.0)), Color32::WHITE);
        }
        let mut picked = None;
        for col in 0..8 {
            for row in 0..rows {
                let cell = Rect::from_min_size(origin + vec2(WELL_W * col as f32, WELL_H * row as f32), vec2(WELL_W, WELL_H));
                let resp = ui.interact(cell, Id::new(("well", which as u8, row, col)), Sense::click());
                let current = if which == Focus::Basic { self.basic_current } else { self.custom_current };
                if self.focus == which && current == (row, col) {
                    // paintCell: frame, focus rectangle, then the colour.
                    focus_rect(ui, cell, p, ppp);
                    let c = colors[row + col * rows];
                    let inside = Rect::from_min_max(cell.min + vec2(4.0, 4.0), cell.max - vec2(4.0, 4.0));
                    fill_device(ui.painter(), snap_fill(inside, ppp), Color32::from_rgb(c[0], c[1], c[2]));
                }
                if resp.clicked() {
                    self.focus = which;
                    if which == Focus::Basic {
                        self.basic_current = (row, col);
                    } else {
                        self.custom_current = (row, col);
                    }
                    picked = Some(row + col * rows);
                }
            }
        }
        picked
    }

    /// `QColorPicker`: hue across, saturation down, at value 200.
    fn picker(&mut self, ui: &Ui, o: Pos2, ppp: f32, p: &Palette) {
        let frame = PICKER.translate(o.to_vec2());
        let tex = self.picker.get_or_insert_with(|| {
            let mut img = ColorImage::new([PICKER_W as usize, PICKER_H as usize], vec![Color32::BLACK; (PICKER_W * PICKER_H) as usize]);
            for y in 0..PICKER_H {
                for x in 0..PICKER_W {
                    let (h, s) = (hue_at(x), sat_at(y));
                    let [r, g, b] = hsv_to_rgb(h, s, 200);
                    img.pixels[(y * PICKER_W + x) as usize] = Color32::from_rgb(r, g, b);
                }
            }
            ui.ctx().load_texture("color_picker", img, TextureOptions::NEAREST)
        });
        let contents = Rect::from_min_size(frame.min + vec2(1.0, 1.0), vec2(PICKER_W as f32, PICKER_H as f32));
        shade_panel(ui, frame, ppp, p, true);
        ui.painter().image(tex.id(), snap_image(contents, ppp), Rect::from_min_max(pos2(0.0, 0.0), pos2(1.0, 1.0)), Color32::WHITE);
        let resp = ui.interact(contents, Id::new("cd_picker"), Sense::click_and_drag());
        if (resp.clicked() || resp.dragged() || resp.drag_started())
            && let Some(pos) = resp.interact_pointer_pos()
        {
            let x = ((pos.x - contents.left()).floor() as i32).clamp(0, PICKER_W - 1);
            let y = ((pos.y - contents.top()).floor() as i32).clamp(0, PICKER_H - 1);
            self.focus = Focus::Other;
            self.set_hsv(hue_at(x), sat_at(y), self.val);
        }
        // The cross: 20 × 2 and 2 × 20 black bars at colPt.
        let pt = col_pt(self.hue, self.sat);
        let painter = ui.painter().with_clip_rect(frame);
        let bar = |x: f32, y: f32, w: f32, h: f32| {
            let r = Rect::from_min_size(contents.min + vec2(x, y), vec2(w, h));
            fill_device(&painter, snap_fill(r, ppp), Color32::BLACK);
        };
        bar(pt.0 as f32 - 9.0, pt.1 as f32, 20.0, 2.0);
        bar(pt.0 as f32, pt.1 as f32 - 9.0, 2.0, 20.0);
    }

    /// `QColorLuminancePicker`: the colour's values down the strip, the arrow at the current one.
    fn luminance(&mut self, ui: &Ui, o: Pos2, ppp: f32, p: &Palette) {
        let r = LUM.translate(o.to_vec2());
        let (coff, foff) = (4.0, 3.0);
        let h = r.height();
        let d = (h - 2.0 * coff - 1.0) as i32;
        let key = (self.hue, self.sat);
        let (wi, hi) = (13usize, (h - 2.0 * foff - 2.0) as usize);
        if self.strip.as_ref().is_none_or(|(_, k)| *k != key) {
            let mut img = ColorImage::new([wi, hi], vec![Color32::BLACK; wi * hi]);
            for y in 0..hi {
                let v = 255 - (y as i32) * 255 / d;
                let [cr, cg, cb] = hsv_to_rgb(self.hue, self.sat, v);
                for x in 0..wi {
                    img.pixels[y * wi + x] = Color32::from_rgb(cr, cg, cb);
                }
            }
            let tex = ui.ctx().load_texture("color_strip", img, TextureOptions::NEAREST);
            self.strip = Some((tex, key));
        }
        if let Some((tex, _)) = &self.strip {
            let dest = Rect::from_min_size(r.min + vec2(1.0, coff), vec2(wi as f32, hi as f32));
            ui.painter().image(tex.id(), snap_image(dest, ppp), Rect::from_min_max(pos2(0.0, 0.0), pos2(1.0, 1.0)), Color32::WHITE);
        }
        let w = r.width() - 5.0;
        shade_panel(ui, Rect::from_min_size(r.min + vec2(0.0, foff), vec2(w, h - 2.0 * foff)), ppp, p, true);
        // eraseRect(w, 0, 5, height()): the palette's window colour.
        let erase = Rect::from_min_size(r.min + vec2(w, 0.0), vec2(5.0, h));
        fill_device(ui.painter(), snap_fill(erase, ppp), p.qt.window);
        let y = coff + ((255 - self.val) * d / 255) as f32;
        let tip = r.min + vec2(w, y);
        aliased_triangle(ui, [tip, tip + vec2(5.0, 5.0), tip + vec2(5.0, -5.0)], p.qt.window_text, ppp);
        let resp = ui.interact(r, Id::new("cd_lum"), Sense::click_and_drag());
        if (resp.clicked() || resp.dragged() || resp.drag_started())
            && let Some(pos) = resp.interact_pointer_pos()
        {
            let yy = (pos.y - r.top()).floor() as i32;
            let v = 255 - (yy - coff as i32) * 255 / d;
            self.focus = Focus::Other;
            self.set_hsv(self.hue, self.sat, v);
        }
    }
}

/// `QColorPicker::huePt` and `satPt` for a pixel of the 220 × 200 field.
fn hue_at(x: i32) -> i32 {
    (360 - x * 360 / (PICKER_W - 1)).clamp(0, 359)
}

fn sat_at(y: i32) -> i32 {
    (255 - y * 255 / (PICKER_H - 1)).clamp(0, 255)
}

/// `colPt`.
fn col_pt(hue: i32, sat: i32) -> (i32, i32) {
    ((360 - hue) * (PICKER_W - 1) / 360, (255 - sat) * (PICKER_H - 1) / 255)
}

/// `QColor::name()` upper-cased the way `QColorShower` shows it: `#rrggbb`.
fn color_html(c: [u8; 3]) -> String {
    format!("#{:02x}{:02x}{:02x}", c[0], c[1], c[2])
}

/// Fill a rectangle of whole device pixels (given in points).
fn fill_device(painter: &egui::Painter, r: Rect, color: Color32) {
    painter.add(Shape::Rect(egui::epaint::RectShape::filled(r, 0.0, color).with_round_to_pixels(false)));
}

/// Where Qt puts a pixmap drawn at a fractional device position: its
/// origin rounded (`floor(v + 0.5)`), its size scaled.
fn snap_image(r: Rect, ppp: f32) -> Rect {
    let px = |v: f32| (v * ppp + 0.5).floor() / ppp;
    Rect::from_min_size(pos2(px(r.left()), px(r.top())), r.size())
}

/// Qt's aliased `fillRect`: the device pixels whose centres are inside `r`.
fn snap_fill(r: Rect, ppp: f32) -> Rect {
    let px = |v: f32| ((v * ppp - 0.5).ceil()) / ppp;
    Rect::from_min_max(pos2(px(r.left()), px(r.top())), pos2(px(r.right()), px(r.bottom())))
}

/// `qDrawShadePanel(lineWidth 1)` at the device pixel ratio: two dark and
/// two light device lines for a sunken frame. Returns the rectangle inside.
fn shade_panel(ui: &Ui, r: Rect, ppp: f32, p: &Palette, sunken: bool) -> Rect {
    let (dark, light) = (p.qt.dark, p.qt.light);
    let (shade, lit) = if sunken { (dark, light) } else { (light, dark) };
    // The widget sits at a (possibly half-pixel) device origin; lines land on
    // floor(origin + v + 0.5).
    let ox = r.left() * ppp;
    let oy = r.top() * ppp;
    let x = ox.floor() as i32 + if ox.fract() >= 0.5 { 1 } else { 0 };
    let y = oy.floor() as i32 + if oy.fract() >= 0.5 { 1 } else { 0 };
    let w = (r.width() * ppp) as i32;
    let h = (r.height() * ppp) as i32;
    let lw = ppp.round() as i32;
    let dev = |x0: i32, y0: i32, x1: i32, y1: i32| {
        Rect::from_min_max(pos2(x0 as f32 / ppp, y0 as f32 / ppp), pos2((x1 + 1) as f32 / ppp, (y1 + 1) as f32 / ppp))
    };
    for i in 0..lw {
        fill_device(ui.painter(), dev(x, y + i, x + w - 2 - i, y + i), shade); // top
        fill_device(ui.painter(), dev(x + i, y + lw.min(i + 1), x + i, y + h - 2 - i), shade); // left
    }
    for i in 0..lw {
        fill_device(ui.painter(), dev(x + i, y + h - 1 - i, x + w - 1, y + h - 1 - i), lit); // bottom
        fill_device(ui.painter(), dev(x + w - 1 - i, y + i, x + w - 1 - i, y + h - lw - 1), lit); // right
    }
    dev(x + lw, y + lw, x + w - 1 - lw, y + h - 1 - lw)
}

/// The colour of a well's sunken frame (measured from the full app).
struct WellColors {
    outer: Color32,
}

impl WellColors {
    fn new(p: &Palette) -> Self {
        let outer = if p.dark { Color32::from_rgb(0x21, 0x21, 0x21) } else { Color32::from_rgb(0x27, 0x27, 0x27) };
        Self { outer }
    }
}

/// A well array (`rows` × 8 cells from `origin`, logical) as Qt paints it at
/// `ppp`: for each cell, Fusion's sunken frame 3 px in — an antialiased
/// band 1.5 device pixels wide from the frame's edge, its outer corners
/// rounded by a device pixel — then the colour filling the frame's inside
/// (aliased). Returns the image and its device origin.
fn wells_image(origin: Pos2, rows: usize, colors: &[[u8; 3]], wc: &WellColors, page: Color32, ppp: f32) -> (ColorImage, (i32, i32)) {
    let ox = (origin.x * ppp).floor() as i32;
    let oy = (origin.y * ppp).floor() as i32;
    let w = (8.0 * WELL_W * ppp).ceil() as i32 + 2;
    let h = (rows as f32 * WELL_H * ppp).ceil() as i32 + 2;
    let mut img = ColorImage::new([w as usize, h as usize], vec![Color32::TRANSPARENT; (w * h) as usize]);
    let band = 1.0 * ppp;
    let radius = 1.0f32;
    for col in 0..8 {
        for row in 0..rows {
            let fx0 = (origin.x + WELL_W * col as f32 + 3.0) * ppp;
            let fy0 = (origin.y + WELL_H * row as f32 + 3.0) * ppp;
            let fx1 = fx0 + (WELL_W - 6.0) * ppp;
            let fy1 = fy0 + (WELL_H - 6.0) * ppp;
            // Inside the outer rounded rectangle and outside the inner one.
            let in_ring = |x: f32, y: f32| {
                if x < fx0 || x > fx1 || y < fy0 || y > fy1 {
                    return false;
                }
                let cx = if x < fx0 + radius { fx0 + radius } else if x > fx1 - radius { fx1 - radius } else { x };
                let cy = if y < fy0 + radius { fy0 + radius } else if y > fy1 - radius { fy1 - radius } else { y };
                if (x - cx).powi(2) + (y - cy).powi(2) > radius * radius {
                    return false;
                }
                x < fx0 + band || x > fx1 - band || y < fy0 + band || y > fy1 - band
            };
            let c = colors[row + col * rows];
            let fill = Color32::from_rgb(c[0], c[1], c[2]);
            for py in (fy0.floor() as i32)..(fy1.ceil() as i32) {
                for px in (fx0.floor() as i32)..(fx1.ceil() as i32) {
                    let (lx, ly) = (px - ox, py - oy);
                    if lx < 0 || ly < 0 || lx >= w || ly >= h {
                        continue;
                    }
                    // The colour: device pixels whose centres are inside the frame's inside.
                    let (cx, cy) = (px as f32 + 0.5, py as f32 + 0.5);
                    let i = (ly * w + lx) as usize;
                    if cx > fx0 + ppp && cx < fx1 - ppp && cy > fy0 + ppp && cy < fy1 - ppp {
                        img.pixels[i] = fill;
                        continue;
                    }
                    const N: i32 = 8;
                    let mut hits = 0;
                    for sy in 0..N {
                        for sx in 0..N {
                            let x = px as f32 + (sx as f32 + 0.5) / N as f32;
                            let y = py as f32 + (sy as f32 + 0.5) / N as f32;
                            if in_ring(x, y) {
                                hits += 1;
                            }
                        }
                    }
                    if hits > 0 {
                        let a = hits as f32 / (N * N) as f32;
                        let mix = |f: u8, b: u8| (f as f32 * a + b as f32 * (1.0 - a)).round() as u8;
                        img.pixels[i] = Color32::from_rgb(mix(wc.outer.r(), page.r()), mix(wc.outer.g(), page.g()), mix(wc.outer.b(), page.b()));
                    }
                }
            }
        }
    }
    (img, (ox, oy))
}

/// Fusion's `PE_FrameFocusRect` on the cell: the highlighted outline at
/// alpha 80 (darker 120) with a light-to-darker gradient at alpha 30
/// inside, as it lands on device pixels at 1.5×: a line on the first row
/// and column, full and half lines on the last ones.
fn focus_rect(ui: &Ui, cell: Rect, p: &Palette, ppp: f32) {
    let highlighted = darker(p.qt.highlight, 125);
    let with_alpha = |c: Color32, a: u8| Color32::from_rgba_unmultiplied(c.r(), c.g(), c.b(), a);
    let pen = with_alpha(darker(highlighted, 120), 80);
    let half_pen = with_alpha(darker(highlighted, 120), 40);
    let top = with_alpha(lighter(highlighted, 160), 30);
    let bottom = with_alpha(highlighted, 30);
    let x0 = (cell.left() * ppp).ceil() as i32;
    let y0 = (cell.top() * ppp).ceil() as i32;
    let xr = (cell.right() * ppp).floor() as i32 - 1;
    let yb = (cell.bottom() * ppp).floor() as i32 - 1;
    let dev = |a: i32, b: i32, c: i32, d: i32| Rect::from_min_max(pos2(a as f32 / ppp, b as f32 / ppp), pos2((c + 1) as f32 / ppp, (d + 1) as f32 / ppp));
    let inside = dev(x0 + 1, y0 + 1, xr - 1, yb - 1);
    let mut mesh = Mesh::default();
    for (pos, c) in [(inside.left_top(), top), (inside.right_top(), top), (inside.right_bottom(), bottom), (inside.left_bottom(), bottom)] {
        mesh.vertices.push(Vertex { pos, uv: egui::epaint::WHITE_UV, color: c });
    }
    mesh.indices.extend([0, 1, 2, 0, 2, 3]);
    let painter = ui.painter();
    painter.add(Shape::mesh(mesh));
    fill_device(painter, dev(x0 + 1, y0, xr, y0), pen);
    fill_device(painter, dev(x0, y0 + 1, x0, yb), pen);
    fill_device(painter, dev(x0 + 1, yb, xr, yb), pen);
    fill_device(painter, dev(xr, y0 + 1, xr, yb - 1), pen);
    fill_device(painter, dev(x0 + 1, yb + 1, xr, yb + 1), half_pen);
    fill_device(painter, dev(xr + 1, y0 + 1, xr + 1, yb), half_pen);
}

/// A filled triangle with a 1 px pen, aliased (pixel centres).
fn aliased_triangle(ui: &Ui, pts: [Pos2; 3], color: Color32, ppp: f32) {
    let d: Vec<(f32, f32)> = pts.iter().map(|p| (p.x * ppp, p.y * ppp)).collect();
    let (minx, maxx) = (d.iter().map(|p| p.0).fold(f32::MAX, f32::min), d.iter().map(|p| p.0).fold(f32::MIN, f32::max));
    let (miny, maxy) = (d.iter().map(|p| p.1).fold(f32::MAX, f32::min), d.iter().map(|p| p.1).fold(f32::MIN, f32::max));
    let half_pen = ppp / 2.0;
    let edge_dist = |px: f32, py: f32, a: (f32, f32), b: (f32, f32)| {
        let (dx, dy) = (b.0 - a.0, b.1 - a.1);
        let len2 = dx * dx + dy * dy;
        let t = (((px - a.0) * dx + (py - a.1) * dy) / len2).clamp(0.0, 1.0);
        ((px - a.0 - t * dx).powi(2) + (py - a.1 - t * dy).powi(2)).sqrt()
    };
    let inside = |px: f32, py: f32| {
        let s = |a: (f32, f32), b: (f32, f32)| (b.0 - a.0) * (py - a.1) - (b.1 - a.1) * (px - a.0);
        let (s0, s1, s2) = (s(d[0], d[1]), s(d[1], d[2]), s(d[2], d[0]));
        (s0 >= 0.0 && s1 >= 0.0 && s2 >= 0.0) || (s0 <= 0.0 && s1 <= 0.0 && s2 <= 0.0)
    };
    for yy in (miny - half_pen).floor() as i32..=(maxy + half_pen).ceil() as i32 {
        for xx in (minx - half_pen).floor() as i32..=(maxx + half_pen).ceil() as i32 {
            let (cx, cy) = (xx as f32 + 0.5, yy as f32 + 0.5);
            let near = (0..3).any(|i| edge_dist(cx, cy, d[i], d[(i + 1) % 3]) <= half_pen);
            if inside(cx, cy) || near {
                let r = Rect::from_min_size(pos2(xx as f32 / ppp, yy as f32 / ppp), vec2(1.0 / ppp, 1.0 / ppp));
                fill_device(ui.painter(), r, color);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hsv_round_trips_like_qcolor() {
        assert_eq!(rgb_to_hsv([0x00, 0xe5, 0xff]), (186, 255, 255));
        assert_eq!(hsv_to_rgb(186, 255, 255), [0, 230, 255]);
        assert_eq!(rgb_to_hsv([128, 128, 128]), (-1, 0, 128));
        assert_eq!(basic_colors()[6], [170, 0, 0]);
    }
}
