//! Qt's layout engine, so Lite's dialogs are laid out (and resize) exactly
//! like the full app's: `qGeomCalc` (qlayoutengine.cpp), `QBoxLayout`,
//! `QGridLayout` (including spanning cells and height-for-width), the
//! `QWidgetItem` size rules (`qSmartMinSize`/`qSmartMaxSize`) and `QSplitter`.
//!
//! A dialog describes its widgets with their Qt size hints and policies,
//! nests them in layouts, and [`Layout::set_geometry`] returns every
//! widget's rectangle in integer logical pixels, as Qt would place them.

use std::collections::HashMap;

/// `QLAYOUTSIZE_MAX`.
pub const LAYOUT_MAX: i32 = 524_287;
/// `QWIDGETSIZE_MAX`.
pub const WIDGET_MAX: i32 = 16_777_215;

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Size {
    pub w: i32,
    pub h: i32,
}

impl Size {
    pub const fn new(w: i32, h: i32) -> Self {
        Size { w, h }
    }

    fn expanded_to(self, o: Size) -> Size {
        Size::new(self.w.max(o.w), self.h.max(o.h))
    }

    fn bounded_to(self, o: Size) -> Size {
        Size::new(self.w.min(o.w), self.h.min(o.h))
    }

    fn pick(self, horizontal: bool) -> i32 {
        if horizontal { self.w } else { self.h }
    }
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct IRect {
    pub x: i32,
    pub y: i32,
    pub w: i32,
    pub h: i32,
}

impl IRect {
    pub const fn new(x: i32, y: i32, w: i32, h: i32) -> Self {
        IRect { x, y, w, h }
    }

    pub fn to_egui(self, origin: egui::Pos2) -> egui::Rect {
        egui::Rect::from_min_size(
            egui::pos2(origin.x + self.x as f32, origin.y + self.y as f32),
            egui::vec2(self.w as f32, self.h as f32),
        )
    }

    pub fn shrink(self, l: i32, t: i32, r: i32, b: i32) -> IRect {
        IRect::new(self.x + l, self.y + t, self.w - l - r, self.h - t - b)
    }
}

/// `QSizePolicy::Policy`.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Policy {
    Fixed,
    Minimum,
    Maximum,
    #[default]
    Preferred,
    Expanding,
    MinimumExpanding,
    Ignored,
}

impl Policy {
    fn grows(self) -> bool {
        !matches!(self, Policy::Fixed | Policy::Maximum)
    }

    fn shrinks(self) -> bool {
        matches!(self, Policy::Maximum | Policy::Preferred | Policy::Expanding | Policy::Ignored)
    }

    fn expands(self) -> bool {
        matches!(self, Policy::Expanding | Policy::MinimumExpanding)
    }
}

/// Alignment of an item in its cell (`Qt::Alignment`), per direction.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Align {
    pub h: Option<HAlign>,
    pub v: Option<VAlign>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum HAlign {
    Left,
    Center,
    Right,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum VAlign {
    Top,
    Center,
    Bottom,
}

impl Align {
    pub const NONE: Align = Align { h: None, v: None };
    pub const LEFT: Align = Align { h: Some(HAlign::Left), v: None };
}

/// Height for a width, for word-wrapping labels (`QLabel::heightForWidth`).
pub type Hfw = std::rc::Rc<dyn Fn(i32) -> i32>;

/// What Qt knows about a widget for laying it out.
#[derive(Clone)]
pub struct WidgetSpec {
    pub id: &'static str,
    pub hint: Size,
    pub min_hint: Size,
    /// `minimumSize()` set on the widget (0 where unset).
    pub min: Size,
    /// `maximumSize()` (`WIDGET_MAX` where unset).
    pub max: Size,
    pub hpolicy: Policy,
    pub vpolicy: Policy,
    pub hstretch: i32,
    pub vstretch: i32,
    pub hfw: Option<Hfw>,
    pub hidden: bool,
}

impl WidgetSpec {
    pub fn new(id: &'static str, hint: Size, hpolicy: Policy, vpolicy: Policy) -> Self {
        WidgetSpec {
            id,
            hint,
            min_hint: hint,
            min: Size::default(),
            max: Size::new(WIDGET_MAX, WIDGET_MAX),
            hpolicy,
            vpolicy,
            hstretch: 0,
            vstretch: 0,
            hfw: None,
            hidden: false,
        }
    }

    pub fn min_hint(mut self, s: Size) -> Self {
        self.min_hint = s;
        self
    }

    pub fn min_size(mut self, s: Size) -> Self {
        self.min = s;
        self
    }

    pub fn min_width(mut self, w: i32) -> Self {
        self.min.w = w;
        self
    }

    pub fn min_height(mut self, h: i32) -> Self {
        self.min.h = h;
        self
    }

    pub fn max_width(mut self, w: i32) -> Self {
        self.max.w = w;
        self
    }

    pub fn max_height(mut self, h: i32) -> Self {
        self.max.h = h;
        self
    }

    pub fn stretch(mut self, h: i32, v: i32) -> Self {
        self.hstretch = h;
        self.vstretch = v;
        self
    }

    pub fn hfw(mut self, f: Hfw) -> Self {
        self.hfw = Some(f);
        self
    }

    pub fn hidden(mut self, hidden: bool) -> Self {
        self.hidden = hidden;
        self
    }
}

/// A widget with its own layout (a group box, a page, a panel): the layout
/// sits inside the widget's contents margins.
#[derive(Clone)]
pub struct Container {
    pub spec: WidgetSpec,
    /// `contentsMargins()` of the widget: left, top, right, bottom.
    pub margins: [i32; 4],
    pub layout: Box<Layout>,
}

#[derive(Clone)]
pub enum Item {
    Widget(WidgetSpec),
    Container(Container),
    Layout(Box<Layout>),
    /// `QSpacerItem`: `addStretch` / `addSpacing`.
    Spacer {
        size: Size,
        hpolicy: Policy,
        vpolicy: Policy,
    },
    Splitter(Splitter),
}

/// `QSplitter`: children share space in proportion to their size hints
/// times their stretch factors, with handles between them.
#[derive(Clone)]
pub struct Splitter {
    pub spec: WidgetSpec,
    pub horizontal: bool,
    pub handle: i32,
    pub children: Vec<Container>,
    /// The sizes chosen by dragging a handle, if any.
    pub sizes: Option<Vec<i32>>,
}

#[derive(Clone)]
pub enum Layout {
    Box(BoxLayout),
    Grid(GridLayout),
    Form(FormLayout),
}

/// `QFormLayout` as Fusion configures it: rows never wrap, fields that are
/// not fixed grow, labels left-aligned, the form at the top left.
#[derive(Clone)]
pub struct FormLayout {
    pub margins: [i32; 4],
    pub hspacing: i32,
    pub vspacing: i32,
    /// `(label, field)` per row; no label is a spanning row (`addRow(widget)`).
    pub rows: Vec<(Option<Item>, Item)>,
}

impl FormLayout {
    pub fn new(margins: [i32; 4], hspacing: i32, vspacing: i32) -> Self {
        FormLayout { margins, hspacing, vspacing, rows: Vec::new() }
    }

    /// `addRow(label, field)`.
    pub fn row(mut self, label: WidgetSpec, field: Item) -> Self {
        self.rows.push((Some(Item::Widget(label)), field));
        self
    }

    /// `addRow(widget)`: the item spans both columns.
    pub fn span(mut self, field: Item) -> Self {
        self.rows.push((None, field));
        self
    }

    pub fn into_item(self) -> Item {
        Item::Layout(Box::new(Layout::Form(self)))
    }

    /// The rows' sizes (`updateSizes`): label and field min, hint and max.
    fn items(&self) -> Vec<(Option<ItemInfo>, ItemInfo)> {
        self.rows.iter().map(|(l, f)| (l.as_ref().map(|l| l.info(Align::NONE)), f.info(Align::NONE))).collect()
    }

    /// `setupVerticalLayoutData`: one chain entry per row after a top
    /// entry, a bottom stretch, and the widest label that fits `width`.
    fn vertical(&self, width: i32) -> (Vec<LayoutStruct>, i32) {
        let items = self.items();
        let mut max_label = 0;
        for (l, f) in &items {
            if let Some(l) = l
                && l.hint.w + f.min.w <= width
            {
                max_label = max_label.max(l.hint.w);
            }
        }
        let mut chain = vec![LayoutStruct::init(0, 0)];
        let mut add_stretch = true;
        for (l, f) in &items {
            let (min1, sh1, max1, s1, e1) =
                l.as_ref().map_or((0, 0, WIDGET_MAX, 0, false), |l| (l.min.h, l.hint.h, l.max.h, l.vstretch, l.exp_v));
            let mut row = LayoutStruct::init(s1.max(f.vstretch), min1.max(f.min.h));
            row.size_hint = sh1.max(f.hint.h);
            row.maximum_size = max1.min(f.max.h);
            row.expansive = e1 || f.exp_v || row.stretch > 0;
            row.empty = false;
            if row.expansive {
                add_stretch = false;
            }
            if chain.len() > 1 {
                let last = chain.len() - 1;
                chain[last].spacing = self.vspacing;
            }
            chain.push(row);
        }
        if add_stretch {
            let mut bottom = LayoutStruct::init(1, 0);
            bottom.expansive = true;
            chain.push(bottom);
        }
        (chain, max_label)
    }

    fn margin_size(&self) -> Size {
        let [l, t, r, b] = self.margins;
        Size::new(l + r, t + b)
    }

    /// `calcSizeHints` with `updateSizes`.
    fn summary(&self) -> Summary {
        let items = self.items();
        let (mut sh_l, mut sh_f, mut min_l, mut min_f) = (0, 0, 0, 0);
        let (mut sh_span, mut min_span) = (0, 0);
        let (mut exp_h, mut exp_v) = (false, false);
        for (l, f) in &items {
            if let Some(l) = l {
                sh_l = sh_l.max(l.hint.w);
                min_l = min_l.max(l.min.w);
                exp_h |= l.exp_h;
                exp_v |= l.exp_v;
                sh_f = sh_f.max(f.hint.w + self.hspacing);
                min_f = min_f.max(f.min.w + self.hspacing);
            } else {
                sh_span = sh_span.max(f.hint.w);
                min_span = min_span.max(f.min.w);
            }
            exp_h |= f.exp_h;
            exp_v |= f.exp_v;
        }
        let (chain, _) = self.vertical(LAYOUT_MAX);
        let m = self.margin_size();
        let h: i32 = chain.iter().map(|c| c.size_hint + c.spacing).sum();
        let mh: i32 = chain.iter().map(|c| c.minimum_size + c.spacing).sum();
        Summary {
            hint: Size::new((sh_l + sh_f).max(sh_span), h) + m,
            min: Size::new((min_l + min_f).max(min_span), mh) + m,
            max: Size::new(LAYOUT_MAX, LAYOUT_MAX),
            exp_h,
            exp_v,
            has_hfw: false,
            empty: self.rows.is_empty(),
        }
    }

    /// `QFormLayout::setGeometry` and `arrangeWidgets`.
    fn set_geometry(&self, r: IRect, out: &mut Geometry) {
        let [l, t, rr, b] = self.margins;
        let cr = r.shrink(l, t, rr, b);
        let (mut chain, max_label) = self.vertical(cr.w);
        let n = chain.len();
        geom_calc(&mut chain, 0, n, cr.y, cr.h, -1);
        let items = self.items();
        for (i, ((label, field), (li, fi))) in self.rows.iter().zip(&items).enumerate() {
            let row = chain[i + 1];
            if let (Some(label), Some(li)) = (label, li) {
                let mut h = row.size;
                if !li.exp_v {
                    // A tall field gets a top-aligned label, "but not too much".
                    h = h.min((li.hint.h * 7 / 4).min(li.max.h));
                }
                let w = max_label.min(li.hint.w);
                label.set_geometry(IRect::new(cr.x, row.pos, w, h), Align::NONE, out);
            }
            let fw = if label.is_some() { cr.w - max_label - self.hspacing } else { cr.w };
            let size = Size::new(fw, row.size).bounded_to(fi.max);
            field.set_geometry(IRect::new(cr.x + cr.w - fw, row.pos, size.w, size.h), Align::NONE, out);
        }
    }
}

#[derive(Clone)]
pub struct BoxLayout {
    pub horizontal: bool,
    pub margins: [i32; 4],
    pub spacing: i32,
    pub items: Vec<(Item, i32, Align)>,
}

#[derive(Clone)]
pub struct GridLayout {
    pub margins: [i32; 4],
    pub hspacing: i32,
    pub vspacing: i32,
    /// `(item, row, column, row span, column span, alignment)`.
    pub items: Vec<(Item, usize, usize, usize, usize, Align)>,
    pub col_stretch: Vec<i32>,
    pub row_stretch: Vec<i32>,
}

impl BoxLayout {
    pub fn h(margins: [i32; 4], spacing: i32) -> Self {
        BoxLayout { horizontal: true, margins, spacing, items: Vec::new() }
    }

    pub fn v(margins: [i32; 4], spacing: i32) -> Self {
        BoxLayout { horizontal: false, margins, spacing, items: Vec::new() }
    }

    /// `QBoxLayout::addItem` (a builder step, not `std::ops::Add`).
    #[allow(clippy::should_implement_trait)]
    pub fn add(mut self, item: Item) -> Self {
        self.items.push((item, 0, Align::NONE));
        self
    }

    pub fn add_stretched(mut self, item: Item, stretch: i32) -> Self {
        self.items.push((item, stretch, Align::NONE));
        self
    }

    pub fn add_aligned(mut self, item: Item, align: Align) -> Self {
        self.items.push((item, 0, align));
        self
    }

    pub fn widget(self, w: WidgetSpec) -> Self {
        self.add(Item::Widget(w))
    }

    /// `addStretch(stretch)`.
    pub fn stretch(mut self, stretch: i32) -> Self {
        let (h, v) =
            if self.horizontal { (Policy::Expanding, Policy::Minimum) } else { (Policy::Minimum, Policy::Expanding) };
        self.items.push((Item::Spacer { size: Size::default(), hpolicy: h, vpolicy: v }, stretch, Align::NONE));
        self
    }

    pub fn into_item(self) -> Item {
        Item::Layout(Box::new(Layout::Box(self)))
    }
}

impl GridLayout {
    pub fn new(margins: [i32; 4], hspacing: i32, vspacing: i32) -> Self {
        GridLayout { margins, hspacing, vspacing, items: Vec::new(), col_stretch: Vec::new(), row_stretch: Vec::new() }
    }

    pub fn add(mut self, item: Item, row: usize, col: usize, row_span: usize, col_span: usize) -> Self {
        self.items.push((item, row, col, row_span, col_span, Align::NONE));
        self
    }

    pub fn widget(self, w: WidgetSpec, row: usize, col: usize) -> Self {
        self.add(Item::Widget(w), row, col, 1, 1)
    }

    pub fn widget_span(self, w: WidgetSpec, row: usize, col: usize, rs: usize, cs: usize) -> Self {
        self.add(Item::Widget(w), row, col, rs, cs)
    }

    pub fn column_stretch(mut self, col: usize, stretch: i32) -> Self {
        if self.col_stretch.len() <= col {
            self.col_stretch.resize(col + 1, 0);
        }
        self.col_stretch[col] = stretch;
        self
    }

    fn rows(&self) -> usize {
        self.items.iter().map(|i| i.1 + i.3).max().unwrap_or(0).max(self.row_stretch.len())
    }

    fn cols(&self) -> usize {
        self.items.iter().map(|i| i.2 + i.4).max().unwrap_or(0).max(self.col_stretch.len())
    }
}

// ------------------------------------------------------------------ qGeomCalc

#[derive(Clone, Copy, Debug)]
struct LayoutStruct {
    stretch: i32,
    size_hint: i32,
    maximum_size: i32,
    minimum_size: i32,
    spacing: i32,
    expansive: bool,
    empty: bool,
    done: bool,
    pos: i32,
    size: i32,
}

impl LayoutStruct {
    fn init(stretch: i32, min: i32) -> Self {
        LayoutStruct {
            stretch,
            size_hint: min,
            maximum_size: LAYOUT_MAX,
            minimum_size: min,
            spacing: 0,
            expansive: false,
            empty: true,
            done: false,
            pos: 0,
            size: 0,
        }
    }

    fn smart_size_hint(&self) -> i32 {
        if self.stretch > 0 { self.minimum_size } else { self.size_hint }
    }

    fn effective_spacer(&self, uniform: i32) -> i32 {
        if uniform >= 0 { uniform } else { self.spacing }
    }
}

fn to_fixed(i: i32) -> i64 {
    i as i64 * 256
}

fn f_round(i: i64) -> i32 {
    // C++ `%` and `/` truncate toward zero; the values are non-negative.
    (if i % 256 < 128 { i / 256 } else { 1 + i / 256 }) as i32
}

/// `qGeomCalc`: sizes and positions for `chain[start..start+count]` in
/// `space` pixels from `pos`.
fn geom_calc(chain: &mut [LayoutStruct], start: usize, count: usize, pos: i32, space: i32, mut spacer: i32) {
    let range = start..start + count;
    let mut c_hint = 0;
    let mut c_min = 0;
    let mut sum_stretch = 0;
    let mut sum_spacing = 0;
    let mut expanding_count = 0;
    let mut all_empty_nonstretch = true;
    let mut pending_spacing = -1;
    let mut spacer_count = 0;
    for d in &mut chain[range.clone()] {
        d.done = false;
        c_hint += d.smart_size_hint();
        c_min += d.minimum_size;
        sum_stretch += d.stretch;
        if !d.empty {
            if pending_spacing >= 0 {
                sum_spacing += pending_spacing;
                spacer_count += 1;
            }
            pending_spacing = d.effective_spacer(spacer);
        }
        if d.expansive {
            expanding_count += 1;
        }
        all_empty_nonstretch = all_empty_nonstretch && d.empty && !d.expansive && d.stretch <= 0;
    }

    let mut extraspace = 0;
    if space < c_min + sum_spacing {
        // Less space than the minimum: take from the biggest first.
        let min_size = c_min + sum_spacing;
        if spacer >= 0 {
            spacer = if min_size > 0 { spacer * space / min_size } else { 0 };
            sum_spacing = spacer * spacer_count;
        }
        let mut mins: Vec<i32> = chain[range.clone()].iter().map(|d| d.minimum_size).collect();
        mins.sort();
        let space_left = space - sum_spacing;
        let (mut sum, mut idx, mut space_used, mut current) = (0, 0usize, 0, 0);
        while idx < count && space_used < space_left {
            current = mins[idx];
            space_used = sum + current * (count - idx) as i32;
            sum += current;
            idx += 1;
        }
        let idx = idx as i32 - 1;
        let deficit = space_used - space_left;
        let items = count as i32 - idx;
        let deficit_per_item = deficit / items;
        let remainder = deficit % items;
        let maxval = current - deficit_per_item;
        let mut rest = 0;
        for d in &mut chain[range.clone()] {
            let mut maxv = maxval;
            rest += remainder;
            if rest >= items {
                maxv -= 1;
                rest -= items;
            }
            d.size = d.minimum_size.min(maxv);
            d.done = true;
        }
    } else if space < c_hint + sum_spacing {
        // Between the minimum and the hint: take equally from each.
        let mut n = count as i32;
        let mut space_left = space - sum_spacing;
        let mut overdraft = c_hint - space_left;
        for d in &mut chain[range.clone()] {
            if !d.done && d.minimum_size >= d.smart_size_hint() {
                d.size = d.smart_size_hint();
                d.done = true;
                space_left -= d.smart_size_hint();
                n -= 1;
            }
        }
        let mut finished = n == 0;
        while !finished {
            finished = true;
            let fp_over = to_fixed(overdraft);
            let mut fp_w: i64 = 0;
            for d in &mut chain[range.clone()] {
                if d.done {
                    continue;
                }
                fp_w += fp_over / n as i64;
                let w = f_round(fp_w);
                d.size = d.smart_size_hint() - w;
                fp_w -= to_fixed(w);
                if d.size < d.minimum_size {
                    d.done = true;
                    d.size = d.minimum_size;
                    finished = false;
                    overdraft -= d.smart_size_hint() - d.minimum_size;
                    n -= 1;
                    break;
                }
            }
        }
        let _ = space_left;
    } else {
        // Extra space.
        let mut n = count as i32;
        let mut space_left = space - sum_spacing;
        for d in &mut chain[range.clone()] {
            if !d.done
                && (d.maximum_size <= d.smart_size_hint()
                    || (!all_empty_nonstretch && d.empty && !d.expansive && d.stretch == 0))
            {
                d.size = d.smart_size_hint();
                d.done = true;
                space_left -= d.size;
                sum_stretch -= d.stretch;
                if d.expansive {
                    expanding_count -= 1;
                }
                n -= 1;
            }
        }
        extraspace = space_left;
        loop {
            let (mut surplus, mut deficit) = (0, 0);
            let fp_space = to_fixed(space_left);
            let mut fp_w: i64 = 0;
            for d in &mut chain[range.clone()] {
                if d.done {
                    continue;
                }
                extraspace = 0;
                if sum_stretch > 0 {
                    fp_w += fp_space * d.stretch as i64 / sum_stretch as i64;
                } else if expanding_count > 0 {
                    fp_w += fp_space * i64::from(d.expansive) / expanding_count as i64;
                } else {
                    fp_w += fp_space / n as i64;
                }
                let w = f_round(fp_w);
                d.size = w;
                fp_w -= to_fixed(w);
                if w < d.smart_size_hint() {
                    deficit += d.smart_size_hint() - w;
                } else if w > d.maximum_size {
                    surplus += w - d.maximum_size;
                }
            }
            if deficit > 0 && surplus <= deficit {
                for d in &mut chain[range.clone()] {
                    if !d.done && d.size < d.smart_size_hint() {
                        d.size = d.smart_size_hint();
                        d.done = true;
                        space_left -= d.smart_size_hint();
                        sum_stretch -= d.stretch;
                        if d.expansive {
                            expanding_count -= 1;
                        }
                        n -= 1;
                    }
                }
            }
            if surplus > 0 && surplus >= deficit {
                for d in &mut chain[range.clone()] {
                    if !d.done && d.size > d.maximum_size {
                        d.size = d.maximum_size;
                        d.done = true;
                        space_left -= d.maximum_size;
                        sum_stretch -= d.stretch;
                        if d.expansive {
                            expanding_count -= 1;
                        }
                        n -= 1;
                    }
                }
            }
            if !(n > 0 && surplus != deficit) {
                break;
            }
        }
        if n == 0 {
            extraspace = space_left;
        }
    }

    // As a last resort, the unwanted space goes equally to the spacers.
    let extra = extraspace / (spacer_count + 2);
    let mut p = pos + extra;
    for d in &mut chain[range] {
        d.pos = p;
        p += d.size;
        if !d.empty {
            p += d.effective_spacer(spacer) + extra;
        }
    }
}

/// `qMaxExpCalc`.
fn max_exp_calc(max: &mut i32, exp: &mut bool, empty: &mut bool, boxmax: i32, boxexp: bool, boxempty: bool) {
    if *exp {
        if boxexp {
            *max = (*max).max(boxmax);
        }
    } else if boxexp || (*empty && (!boxempty || *max == 0)) {
        *max = boxmax;
    } else if *empty == boxempty {
        *max = (*max).min(boxmax);
    }
    *exp = *exp || boxexp;
    *empty = *empty && boxempty;
}

// ------------------------------------------------------------------ items

/// The layout's view of an item (`QLayoutItem`).
struct ItemInfo {
    hint: Size,
    min: Size,
    max: Size,
    exp_h: bool,
    exp_v: bool,
    empty: bool,
    hstretch: i32,
    vstretch: i32,
}

/// `qSmartMinSize`.
fn smart_min(s: &WidgetSpec) -> Size {
    let pick = |pol: Policy, hint: i32, min_hint: i32| {
        if pol == Policy::Ignored {
            0
        } else if pol.shrinks() {
            min_hint
        } else {
            hint.max(min_hint)
        }
    };
    let mut out = Size::new(pick(s.hpolicy, s.hint.w, s.min_hint.w), pick(s.vpolicy, s.hint.h, s.min_hint.h));
    out = out.bounded_to(s.max);
    if s.min.w > 0 {
        out.w = s.min.w;
    }
    if s.min.h > 0 {
        out.h = s.min.h;
    }
    out.expanded_to(Size::default())
}

/// `qSmartMaxSize`.
fn smart_max(s: &WidgetSpec, align: Align) -> Size {
    if align.h.is_some() && align.v.is_some() {
        return Size::new(LAYOUT_MAX, LAYOUT_MAX);
    }
    let mut out = s.max;
    let hint = s.hint.expanded_to(s.min_hint);
    if out.w == WIDGET_MAX && align.h.is_none() && !s.hpolicy.grows() {
        out.w = hint.w;
    }
    if out.h == WIDGET_MAX && align.v.is_none() && !s.vpolicy.grows() {
        out.h = hint.h;
    }
    if align.h.is_some() {
        out.w = LAYOUT_MAX;
    }
    if align.v.is_some() {
        out.h = LAYOUT_MAX;
    }
    out
}

/// `QWidgetItem::sizeHint`.
fn widget_hint(s: &WidgetSpec) -> Size {
    let mut out = s.hint.expanded_to(s.min_hint).bounded_to(s.max).expanded_to(s.min);
    if s.hpolicy == Policy::Ignored {
        out.w = 0;
    }
    if s.vpolicy == Policy::Ignored {
        out.h = 0;
    }
    out
}

impl Container {
    /// The widget's spec with its hints taken from its layout
    /// (`QLayout::totalSizeHint`, `totalMinimumSize`), and whether the
    /// layout expands.
    fn resolved_with(&self) -> (WidgetSpec, (bool, bool)) {
        let [l, t, r, b] = self.margins;
        let side = Size::new(l + r, t + b);
        let sum = self.layout.summary();
        let mut spec = self.spec.clone();
        let mut hint = sum.hint;
        if sum.has_hfw {
            // Qt passes the hint width plus the side margins here.
            hint.h = self.layout.height_for_width(hint.w + l + r);
        }
        spec.hint = hint + side;
        spec.min_hint = sum.min + side;
        (spec, (sum.exp_h, sum.exp_v))
    }

    fn resolved(&self) -> WidgetSpec {
        self.resolved_with().0
    }

    fn info(&self, align: Align) -> ItemInfo {
        let (spec, (lh, lv)) = self.resolved_with();
        let mut info = widget_info(&spec, align);
        if !info.empty {
            info.exp_h |= spec.hpolicy.grows() && lh && align.h.is_none();
            info.exp_v |= spec.vpolicy.grows() && lv && align.v.is_none();
        }
        info
    }

    fn hfw(&self, w: i32) -> i32 {
        let [l, t, r, b] = self.margins;
        let h = self.layout.height_for_width(w - l - r) + t + b;
        h.clamp(self.spec.min.h, self.spec.max.h)
    }

    fn set_geometry(&self, r: IRect, out: &mut Geometry) {
        out.insert(self.spec.id, r);
        let [l, t, rr, b] = self.margins;
        self.layout.set_geometry(r.shrink(l, t, rr, b), out);
    }
}

impl std::ops::Add for Size {
    type Output = Size;
    fn add(self, o: Size) -> Size {
        Size::new(self.w + o.w, self.h + o.h)
    }
}

fn widget_info(s: &WidgetSpec, align: Align) -> ItemInfo {
    if s.hidden {
        // `QWidgetItem::isEmpty`: a hidden widget has no size at all.
        return ItemInfo {
            hint: Size::default(),
            min: Size::default(),
            max: Size::default(),
            exp_h: false,
            exp_v: false,
            empty: true,
            hstretch: s.hstretch,
            vstretch: s.vstretch,
        };
    }
    ItemInfo {
        hint: widget_hint(s),
        min: smart_min(s),
        max: smart_max(s, align),
        exp_h: !s.hidden && s.hpolicy.expands() && align.h.is_none(),
        exp_v: !s.hidden && s.vpolicy.expands() && align.v.is_none(),
        empty: s.hidden,
        hstretch: s.hstretch,
        vstretch: s.vstretch,
    }
}

impl Splitter {
    fn info(&self, align: Align) -> ItemInfo {
        // QSplitter::sizeHint / minimumSizeHint: the children along the
        // orientation plus handles, the largest across.
        let mut hint = Size::default();
        let mut min = Size::default();
        for (i, c) in self.children.iter().enumerate() {
            let s = c.resolved();
            let handle = if i == 0 { 0 } else { self.handle };
            let (h, m) = (widget_hint(&s), smart_min(&s));
            if self.horizontal {
                hint = Size::new(hint.w + h.w + handle, hint.h.max(h.h));
                min = Size::new(min.w + m.w + handle, min.h.max(m.h));
            } else {
                hint = Size::new(hint.w.max(h.w), hint.h + h.h + handle);
                min = Size::new(min.w.max(m.w), min.h + m.h + handle);
            }
        }
        let mut spec = self.spec.clone();
        spec.hint = hint;
        spec.min_hint = min;
        widget_info(&spec, align)
    }

    /// `QSplitterPrivate::doResize`.
    fn set_geometry(&self, r: IRect, out: &mut Geometry) {
        out.insert(self.spec.id, r);
        let horizontal = self.horizontal;
        let no_stretch = self.children.iter().all(|c| if horizontal { c.spec.hstretch } else { c.spec.vstretch } == 0);
        let mut a = Vec::new();
        for (i, c) in self.children.iter().enumerate() {
            let mut handle = LayoutStruct::init(0, 0);
            if i == 0 {
                handle.maximum_size = 0;
            } else {
                handle.size_hint = self.handle;
                handle.minimum_size = self.handle;
                handle.maximum_size = self.handle;
                handle.empty = false;
            }
            a.push(handle);
            let spec = c.resolved();
            let mut d = LayoutStruct::init(0, 0);
            d.minimum_size = smart_min(&spec).pick(horizontal);
            d.maximum_size = smart_max(&spec, Align::NONE).pick(horizontal);
            d.empty = false;
            let sf = if horizontal { spec.hstretch } else { spec.vstretch };
            let size = match &self.sizes {
                Some(sizes) => sizes[i],
                None => spec.hint.pick(horizontal) * if no_stretch { 1 } else { sf },
            };
            if no_stretch || sf != 0 {
                d.stretch = size;
                d.size_hint = d.minimum_size;
                d.expansive = true;
            } else {
                d.size_hint = size.max(d.minimum_size);
            }
            a.push(d);
        }
        let (pos, space) = if horizontal { (r.x, r.w) } else { (r.y, r.h) };
        let n = a.len();
        geom_calc(&mut a, 0, n, pos, space, 0);
        for (i, c) in self.children.iter().enumerate() {
            let d = a[2 * i + 1];
            let rect =
                if horizontal { IRect::new(d.pos, r.y, d.size, r.h) } else { IRect::new(r.x, d.pos, r.w, d.size) };
            c.set_geometry(rect, out);
            if i > 0 {
                let h = a[2 * i];
                let handle =
                    if horizontal { IRect::new(h.pos, r.y, h.size, r.h) } else { IRect::new(r.x, h.pos, r.w, h.size) };
                out.insert_handle(self.spec.id, i, handle);
            }
        }
    }
}

impl Item {
    fn info(&self, align: Align) -> ItemInfo {
        match self {
            Item::Widget(s) => widget_info(s, align),
            Item::Container(c) => c.info(align),
            Item::Splitter(s) => s.info(align),
            Item::Layout(l) => {
                let sum = l.summary();
                let mut max = sum.max;
                if align.h.is_some() {
                    max.w = LAYOUT_MAX;
                }
                if align.v.is_some() {
                    max.h = LAYOUT_MAX;
                }
                ItemInfo {
                    hint: sum.hint,
                    min: sum.min,
                    max,
                    exp_h: sum.exp_h && align.h.is_none(),
                    exp_v: sum.exp_v && align.v.is_none(),
                    empty: sum.empty,
                    hstretch: 0,
                    vstretch: 0,
                }
            }
            Item::Spacer { size, hpolicy, vpolicy } => {
                let dim = |pol: Policy, v: i32, shrink: bool| {
                    if shrink && pol.shrinks() || !shrink && pol.grows() {
                        if shrink { 0 } else { LAYOUT_MAX }
                    } else {
                        v
                    }
                };
                ItemInfo {
                    hint: *size,
                    min: Size::new(dim(*hpolicy, size.w, true), dim(*vpolicy, size.h, true)),
                    max: Size::new(dim(*hpolicy, size.w, false), dim(*vpolicy, size.h, false)),
                    exp_h: hpolicy.expands(),
                    exp_v: vpolicy.expands(),
                    empty: true,
                    hstretch: 0,
                    vstretch: 0,
                }
            }
        }
    }

    fn has_hfw(&self) -> bool {
        match self {
            Item::Widget(s) => !s.hidden && s.hfw.is_some(),
            Item::Container(c) => !c.spec.hidden && c.layout.has_hfw(),
            Item::Layout(l) => l.has_hfw(),
            Item::Spacer { .. } | Item::Splitter(_) => false,
        }
    }

    /// `QLayoutItem::heightForWidth`.
    fn height_for_width(&self, w: i32) -> i32 {
        match self {
            Item::Widget(s) => {
                let h = s.hfw.as_ref().map_or(s.hint.h, |f| f(w));
                h.clamp(s.min.h, s.max.h).max(0)
            }
            Item::Container(c) => c.hfw(w),
            Item::Layout(l) => l.height_for_width(w),
            _ => -1,
        }
    }

    /// `QWidgetItem::setGeometry` (alignment, maximum size) and nested layouts.
    fn set_geometry(&self, r: IRect, align: Align, out: &mut Geometry) {
        match self {
            Item::Layout(l) => l.set_geometry(r, out),
            Item::Spacer { .. } => {}
            _ => {
                let info = self.info(align);
                if info.empty {
                    return;
                }
                let mut s = Size::new(r.w.min(info.max.w), r.h.min(info.max.h));
                if align.h.is_some() || align.v.is_some() {
                    let pref = info.hint;
                    if align.h.is_some() {
                        s.w = s.w.min(pref.w);
                    }
                    if align.v.is_some() {
                        s.h = s.h.min(if self.has_hfw() { self.height_for_width(s.w) } else { pref.h });
                    }
                }
                let x = match align.h {
                    Some(HAlign::Right) => r.x + (r.w - s.w),
                    Some(HAlign::Left) => r.x,
                    _ => r.x + (r.w - s.w) / 2,
                };
                let y = match align.v {
                    Some(VAlign::Bottom) => r.y + (r.h - s.h),
                    Some(VAlign::Top) => r.y,
                    _ => r.y + (r.h - s.h) / 2,
                };
                let rect = IRect::new(x, y, s.w, s.h);
                match self {
                    Item::Widget(spec) => {
                        out.insert(spec.id, rect);
                    }
                    Item::Container(c) => c.set_geometry(rect, out),
                    Item::Splitter(sp) => sp.set_geometry(rect, out),
                    _ => {}
                }
            }
        }
    }
}

// ------------------------------------------------------------------ layouts

/// The computed geometry: each widget's rectangle, plus splitter handles.
#[derive(Default, Debug, Clone)]
pub struct Geometry {
    pub rects: HashMap<&'static str, IRect>,
    pub handles: HashMap<(&'static str, usize), IRect>,
}

impl Geometry {
    fn insert(&mut self, id: &'static str, r: IRect) {
        self.rects.insert(id, r);
    }

    fn insert_handle(&mut self, id: &'static str, i: usize, r: IRect) {
        self.handles.insert((id, i), r);
    }

    pub fn get(&self, id: &str) -> Option<IRect> {
        self.rects.get(id).copied()
    }

    /// The rectangle of `id`; a zero rectangle if it was not laid out.
    pub fn rect(&self, id: &str) -> IRect {
        self.get(id).unwrap_or_default()
    }
}

struct BoxGeom {
    a: Vec<LayoutStruct>,
    hint: Size,
    min: Size,
    max: Size,
    exp_h: bool,
    exp_v: bool,
    has_hfw: bool,
    empty: bool,
}

impl BoxLayout {
    /// `QBoxLayoutPrivate::setupGeom`.
    fn setup(&self) -> BoxGeom {
        let horz = self.horizontal;
        let (mut maxw, mut maxh) = if horz { (0, LAYOUT_MAX) } else { (LAYOUT_MAX, 0) };
        let (mut minw, mut minh, mut hintw, mut hinth) = (0, 0, 0, 0);
        let (mut horexp, mut verexp) = (false, false);
        let mut has_hfw = false;
        let mut a = Vec::with_capacity(self.items.len());
        let mut previous_non_empty: Option<usize> = None;
        let mut empty = true;
        for (i, (item, stretch, align)) in self.items.iter().enumerate() {
            let info = item.info(*align);
            let mut spacing = 0;
            if !info.empty {
                spacing = if previous_non_empty.is_some() { self.spacing } else { 0 };
                if let Some(p) = previous_non_empty {
                    a[p] = LayoutStruct { spacing, ..a[p] };
                }
                previous_non_empty = Some(i);
            }
            let ignore = info.empty && matches!(item, Item::Widget(_) | Item::Container(_));
            let mut dummy = true;
            let mut d = LayoutStruct::init(0, 0);
            if horz {
                let expand = info.exp_h || *stretch > 0;
                horexp |= expand;
                maxw += spacing + info.max.w;
                minw += spacing + info.min.w;
                hintw += spacing + info.hint.w;
                if !ignore {
                    max_exp_calc(&mut maxh, &mut verexp, &mut dummy, info.max.h, info.exp_v, info.empty);
                }
                minh = minh.max(info.min.h);
                hinth = hinth.max(info.hint.h);
                d.size_hint = info.hint.w;
                d.maximum_size = info.max.w;
                d.minimum_size = info.min.w;
                d.expansive = expand;
                d.stretch = if *stretch != 0 { *stretch } else { info.hstretch };
            } else {
                let expand = info.exp_v || *stretch > 0;
                verexp |= expand;
                maxh += spacing + info.max.h;
                minh += spacing + info.min.h;
                hinth += spacing + info.hint.h;
                if !ignore {
                    max_exp_calc(&mut maxw, &mut horexp, &mut dummy, info.max.w, info.exp_h, info.empty);
                }
                minw = minw.max(info.min.w);
                hintw = hintw.max(info.hint.w);
                d.size_hint = info.hint.h;
                d.maximum_size = info.max.h;
                d.minimum_size = info.min.h;
                d.expansive = expand;
                d.stretch = if *stretch != 0 { *stretch } else { info.vstretch };
            }
            d.empty = info.empty;
            d.spacing = 0;
            has_hfw |= item.has_hfw();
            empty &= info.empty;
            a.push(d);
        }
        maxw = maxw.min(LAYOUT_MAX);
        maxh = maxh.min(LAYOUT_MAX);
        // `setupGeom`: the maximum grows to the minimum before it bounds the hint.
        let min = Size::new(minw, minh);
        let max = Size::new(maxw, maxh).expanded_to(min);
        let hint = Size::new(hintw, hinth).expanded_to(min).bounded_to(max);
        BoxGeom { a, hint, min, max, exp_h: horexp, exp_v: verexp, has_hfw, empty }
    }

    fn margin_size(&self) -> Size {
        let [l, t, r, b] = self.margins;
        Size::new(l + r, t + b)
    }

    /// `QBoxLayoutPrivate::calcHfw` plus margins.
    fn height_for_width(&self, w: i32) -> i32 {
        let g = self.setup();
        if !g.has_hfw {
            return -1;
        }
        let [l, t, r, b] = self.margins;
        let w = w - l - r;
        let mut a = g.a;
        let mut h = 0;
        if self.horizontal {
            let n = a.len();
            geom_calc(&mut a, 0, n, 0, w, -1);
            for (i, (item, _, _)) in self.items.iter().enumerate() {
                let v = if item.has_hfw() { item.height_for_width(a[i].size) } else { item.info(Align::NONE).hint.h };
                h = h.max(v);
            }
        } else {
            for (i, (item, _, align)) in self.items.iter().enumerate() {
                h += if item.has_hfw() { item.height_for_width(w) } else { item.info(*align).hint.h };
                h += a[i].spacing;
            }
        }
        h + t + b
    }

    fn set_geometry(&self, r: IRect, out: &mut Geometry) {
        let g = self.setup();
        let [l, t, rr, b] = self.margins;
        let s = r.shrink(l, t, rr, b);
        let mut a = g.a;
        let (pos, space) = if self.horizontal { (s.x, s.w) } else { (s.y, s.h) };
        if g.has_hfw && !self.horizontal {
            for (i, (item, _, align)) in self.items.iter().enumerate() {
                if item.has_hfw() {
                    let info = item.info(*align);
                    let width = s.w.clamp(info.min.w, info.max.w.max(info.min.w));
                    let h = item.height_for_width(width);
                    a[i].size_hint = h;
                    a[i].minimum_size = h;
                }
            }
        }
        let n = a.len();
        geom_calc(&mut a, 0, n, pos, space, -1);
        for (i, (item, _, align)) in self.items.iter().enumerate() {
            let rect = if self.horizontal {
                IRect::new(a[i].pos, s.y, a[i].size, s.h)
            } else {
                IRect::new(s.x, a[i].pos, s.w, a[i].size)
            };
            item.set_geometry(rect, *align, out);
        }
    }
}

struct GridData {
    rows: Vec<LayoutStruct>,
    cols: Vec<LayoutStruct>,
    has_hfw: bool,
    empty: bool,
}

impl GridLayout {
    fn stretch_of(v: &[i32], i: usize) -> i32 {
        v.get(i).copied().unwrap_or(0)
    }

    /// `QGridLayoutPrivate::setupLayoutData`.
    fn setup(&self) -> GridData {
        let (rr, cc) = (self.rows(), self.cols());
        let mut rows: Vec<LayoutStruct> = (0..rr)
            .map(|i| {
                let st = Self::stretch_of(&self.row_stretch, i);
                let mut d = LayoutStruct::init(st, 0);
                d.maximum_size = if st != 0 { LAYOUT_MAX } else { 0 };
                d
            })
            .collect();
        let mut cols: Vec<LayoutStruct> = (0..cc)
            .map(|i| {
                let st = Self::stretch_of(&self.col_stretch, i);
                let mut d = LayoutStruct::init(st, 0);
                d.maximum_size = if st != 0 { LAYOUT_MAX } else { 0 };
                d
            })
            .collect();
        let mut has_hfw = false;
        let mut has_multi = false;
        let mut grid: Vec<Option<usize>> = vec![None; rr * cc];
        let infos: Vec<ItemInfo> = self.items.iter().map(|(item, .., align)| item.info(*align)).collect();
        let empty = infos.iter().all(|i| i.empty);
        for (i, (item, r, c, rs, cs, _)) in self.items.iter().enumerate() {
            let info = &infos[i];
            has_hfw |= item.has_hfw();
            let skip = info.empty && matches!(item, Item::Widget(_) | Item::Container(_));
            if *rs == 1 {
                if !skip {
                    let d = &mut rows[*r];
                    if Self::stretch_of(&self.row_stretch, *r) == 0 {
                        d.stretch = d.stretch.max(info.vstretch);
                    }
                    d.size_hint = d.size_hint.max(info.hint.h);
                    d.minimum_size = d.minimum_size.max(info.min.h);
                    max_exp_calc(
                        &mut d.maximum_size,
                        &mut d.expansive,
                        &mut d.empty,
                        info.max.h,
                        info.exp_v,
                        info.empty,
                    );
                }
            } else {
                init_empty_multi(&mut rows, *r, r + rs - 1);
                has_multi = true;
            }
            if *cs == 1 {
                if !skip {
                    let d = &mut cols[*c];
                    if Self::stretch_of(&self.col_stretch, *c) == 0 {
                        d.stretch = d.stretch.max(info.hstretch);
                    }
                    d.size_hint = d.size_hint.max(info.hint.w);
                    d.minimum_size = d.minimum_size.max(info.min.w);
                    max_exp_calc(
                        &mut d.maximum_size,
                        &mut d.expansive,
                        &mut d.empty,
                        info.max.w,
                        info.exp_h,
                        info.empty,
                    );
                }
            } else {
                init_empty_multi(&mut cols, *c, c + cs - 1);
                has_multi = true;
            }
            for rr_ in *r..r + rs {
                for cc_ in *c..c + cs {
                    grid[rr_ * cc + cc_] = Some(i);
                }
            }
        }
        setup_spacings(&mut cols, &grid, rr, cc, self.hspacing, true);
        setup_spacings(&mut rows, &grid, rr, cc, self.vspacing, false);
        if has_multi {
            for (i, (_, r, c, rs, cs, _)) in self.items.iter().enumerate() {
                let info = &infos[i];
                if *rs > 1 {
                    distribute_multi_box(
                        &mut rows,
                        *r,
                        r + rs - 1,
                        info.min.h,
                        info.hint.h,
                        &self.row_stretch,
                        info.vstretch,
                    );
                }
                if *cs > 1 {
                    distribute_multi_box(
                        &mut cols,
                        *c,
                        c + cs - 1,
                        info.min.w,
                        info.hint.w,
                        &self.col_stretch,
                        info.hstretch,
                    );
                }
            }
        }
        for d in rows.iter_mut().chain(cols.iter_mut()) {
            d.expansive = d.expansive || d.stretch > 0;
        }
        GridData { rows, cols, has_hfw, empty }
    }

    fn margin_size(&self) -> Size {
        let [l, t, r, b] = self.margins;
        Size::new(l + r, t + b)
    }

    /// `setupHfwLayoutData` + `recalcHFW`: row data for column positions in `cols`.
    fn hfw_rows(&self, data: &GridData, cols: &[LayoutStruct]) -> Vec<LayoutStruct> {
        let rr = data.rows.len();
        let mut r_data: Vec<LayoutStruct> =
            data.rows.iter().map(|d| LayoutStruct { minimum_size: 0, size_hint: 0, ..*d }).collect();
        for pass in 0..2 {
            for (item, r1, c1, rs, cs, align) in &self.items {
                let (r2, c2) = (r1 + rs - 1, c1 + cs - 1);
                let w = cols[c2].pos + cols[c2].size - cols[*c1].pos;
                let info = item.info(*align);
                if *rs == 1 {
                    if pass == 0 {
                        let skip = info.empty && matches!(item, Item::Widget(_) | Item::Container(_));
                        if skip {
                            continue;
                        }
                        let (hint, min) = if item.has_hfw() {
                            let h = item.height_for_width(w);
                            (h, h)
                        } else {
                            (info.hint.h, info.min.h)
                        };
                        r_data[*r1].size_hint = r_data[*r1].size_hint.max(hint);
                        r_data[*r1].minimum_size = r_data[*r1].minimum_size.max(min);
                    }
                } else if pass == 0 {
                    init_empty_multi(&mut r_data, *r1, r2);
                } else {
                    let (mut hint, mut min) = (info.hint.h, info.min.h);
                    if item.has_hfw() {
                        let h = item.height_for_width(w);
                        hint = hint.max(h);
                        min = min.max(h);
                    }
                    distribute_multi_box(&mut r_data, *r1, r2, min, hint, &self.row_stretch, info.vstretch);
                }
            }
        }
        for d in &mut r_data {
            d.expansive = d.expansive || d.stretch > 0;
        }
        let _ = rr;
        r_data
    }

    fn height_for_width(&self, w: i32) -> i32 {
        let data = self.setup();
        if !data.has_hfw {
            return -1;
        }
        let [l, t, r, b] = self.margins;
        let mut cols = data.cols.clone();
        let n = cols.len();
        geom_calc(&mut cols, 0, n, 0, w - l - r, -1);
        let rows = self.hfw_rows(&data, &cols);
        let h: i32 = rows.iter().map(|d| d.size_hint + d.spacing).sum();
        h.min(LAYOUT_MAX) + t + b
    }

    fn set_geometry(&self, r: IRect, out: &mut Geometry) {
        let data = self.setup();
        let [l, t, rr_, b] = self.margins;
        let r = r.shrink(l, t, rr_, b);
        let mut cols = data.cols.clone();
        let nc = cols.len();
        geom_calc(&mut cols, 0, nc, r.x, r.w, -1);
        let mut rows = if data.has_hfw { self.hfw_rows(&data, &cols) } else { data.rows.clone() };
        let nr = rows.len();
        geom_calc(&mut rows, 0, nr, r.y, r.h, -1);
        for (item, r1, c1, rs, cs, align) in &self.items {
            let (r2, c2) = (r1 + rs - 1, c1 + cs - 1);
            let x = cols[*c1].pos;
            let y = rows[*r1].pos;
            let w = cols[c2].pos + cols[c2].size - x;
            let h = rows[r2].pos + rows[r2].size - y;
            item.set_geometry(IRect::new(x, y, w, h), *align, out);
        }
    }
}

fn init_empty_multi(chain: &mut [LayoutStruct], start: usize, end: usize) {
    for d in &mut chain[start..=end] {
        if d.empty && d.maximum_size == 0 {
            d.maximum_size = WIDGET_MAX;
        }
        d.empty = false;
    }
}

/// `QGridLayoutPrivate::setupSpacings` with a fixed spacing.
fn setup_spacings(
    chain: &mut [LayoutStruct],
    grid: &[Option<usize>],
    rr: usize,
    cc: usize,
    spacing: i32,
    horizontal: bool,
) {
    let (lines, along) = if horizontal { (rr, cc) } else { (cc, rr) };
    for line in 0..lines {
        let mut previous_box: Option<Option<usize>> = None;
        let mut previous: Option<usize> = None;
        for k in 0..along {
            if chain[k].empty {
                continue;
            }
            let b = if horizontal { grid[line * cc + k] } else { grid[k * cc + line] };
            if let Some(p) = previous
                && (b.is_none() || previous_box != Some(b))
                && spacing > chain[p].spacing
            {
                chain[p].spacing = spacing;
            }
            previous_box = Some(b);
            previous = Some(k);
        }
    }
}

/// `distributeMultiBox`.
fn distribute_multi_box(
    chain: &mut [LayoutStruct],
    start: usize,
    end: usize,
    min_size: i32,
    size_hint: i32,
    stretch_array: &[i32],
    stretch: i32,
) {
    let (mut w, mut wh, mut max) = (0, 0, 0);
    for (i, d) in chain.iter_mut().enumerate().take(end + 1).skip(start) {
        w += d.minimum_size;
        wh += d.size_hint;
        max += d.maximum_size;
        if stretch_array.get(i).copied().unwrap_or(0) == 0 {
            d.stretch = d.stretch.max(stretch);
        }
        if i != end {
            w += d.spacing;
            wh += d.spacing;
            max += d.spacing;
        }
    }
    let count = end - start + 1;
    if max < min_size {
        geom_calc(chain, start, count, 0, min_size, -1);
        let mut pos = 0;
        for i in start..=end {
            let next = if i == end { min_size } else { chain[i + 1].pos };
            let mut real = next - pos;
            if i != end {
                real -= chain[i].spacing;
            }
            let d = &mut chain[i];
            if d.minimum_size < real {
                d.minimum_size = real;
            }
            if d.maximum_size < d.minimum_size {
                d.maximum_size = d.minimum_size;
            }
            pos = next;
        }
    } else if w < min_size {
        geom_calc(chain, start, count, 0, min_size, -1);
        for d in &mut chain[start..=end] {
            if d.minimum_size < d.size {
                d.minimum_size = d.size;
            }
        }
    }
    if wh < size_hint {
        geom_calc(chain, start, count, 0, size_hint, -1);
        for d in &mut chain[start..=end] {
            if d.size_hint < d.size {
                d.size_hint = d.size;
            }
        }
    }
}

/// A layout's size hints in one pass (`sizeHint`, `minimumSize`,
/// `maximumSize`, `expandingDirections`, `hasHeightForWidth`).
#[derive(Clone, Copy, Debug)]
pub struct Summary {
    pub hint: Size,
    pub min: Size,
    pub max: Size,
    pub exp_h: bool,
    pub exp_v: bool,
    pub has_hfw: bool,
    pub empty: bool,
}

impl Layout {
    pub fn summary(&self) -> Summary {
        let cap = Size::new(LAYOUT_MAX, LAYOUT_MAX);
        match self {
            Layout::Box(b) => {
                let g = b.setup();
                let m = b.margin_size();
                Summary {
                    hint: g.hint + m,
                    min: g.min + m,
                    max: (g.max + m).bounded_to(cap),
                    exp_h: g.exp_h,
                    exp_v: g.exp_v,
                    has_hfw: g.has_hfw,
                    empty: g.empty,
                }
            }
            Layout::Grid(g) => {
                let d = g.setup();
                let m = g.margin_size();
                let sum = |v: &[LayoutStruct], f: fn(&LayoutStruct) -> i32| {
                    v.iter().map(|x| f(x).saturating_add(x.spacing)).fold(0i32, i32::saturating_add).min(LAYOUT_MAX)
                };
                Summary {
                    hint: Size::new(sum(&d.cols, |x| x.size_hint), sum(&d.rows, |x| x.size_hint)) + m,
                    min: Size::new(sum(&d.cols, |x| x.minimum_size), sum(&d.rows, |x| x.minimum_size)) + m,
                    max: (Size::new(sum(&d.cols, |x| x.maximum_size), sum(&d.rows, |x| x.maximum_size)) + m)
                        .bounded_to(cap),
                    exp_h: d.cols.iter().any(|c| c.expansive),
                    exp_v: d.rows.iter().any(|r| r.expansive),
                    has_hfw: d.has_hfw,
                    empty: d.empty,
                }
            }
            Layout::Form(f) => f.summary(),
        }
    }

    pub fn size_hint(&self) -> Size {
        self.summary().hint
    }

    pub fn minimum_size(&self) -> Size {
        self.summary().min
    }

    fn has_hfw(&self) -> bool {
        match self {
            Layout::Box(b) => b.items.iter().any(|(i, ..)| i.has_hfw()),
            Layout::Grid(g) => g.items.iter().any(|(i, ..)| i.has_hfw()),
            Layout::Form(_) => false,
        }
    }

    pub fn height_for_width(&self, w: i32) -> i32 {
        match self {
            Layout::Box(b) => b.height_for_width(w),
            Layout::Grid(g) => g.height_for_width(w),
            Layout::Form(f) => f.summary().hint.h,
        }
    }

    /// Lays everything out in `r` (the parent widget's contents rectangle).
    pub fn set_geometry(&self, r: IRect, out: &mut Geometry) {
        match self {
            Layout::Box(b) => b.set_geometry(r, out),
            Layout::Grid(g) => g.set_geometry(r, out),
            Layout::Form(f) => f.set_geometry(r, out),
        }
    }

    /// The geometry of a whole window whose contents are `size`.
    pub fn layout_window(&self, size: Size) -> Geometry {
        let mut out = Geometry::default();
        self.set_geometry(IRect::new(0, 0, size.w, size.h), &mut out);
        out
    }
}

impl From<BoxLayout> for Layout {
    fn from(b: BoxLayout) -> Self {
        Layout::Box(b)
    }
}

impl From<FormLayout> for Layout {
    fn from(f: FormLayout) -> Self {
        Layout::Form(f)
    }
}

impl From<GridLayout> for Layout {
    fn from(g: GridLayout) -> Self {
        Layout::Grid(g)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn button(id: &'static str, w: i32) -> WidgetSpec {
        WidgetSpec::new(id, Size::new(w, 46), Policy::Minimum, Policy::Fixed)
    }

    fn label(id: &'static str, w: i32) -> WidgetSpec {
        WidgetSpec::new(id, Size::new(w, 16), Policy::Preferred, Policy::Preferred)
    }

    /// The Single Station tab's parameter row: five widgets share 1468 px.
    #[test]
    fn box_layout_shares_like_qt() {
        let row = BoxLayout::h([0; 4], 8)
            .widget(label("date_label", 28))
            .widget(
                WidgetSpec::new("date", Size::new(140, 45), Policy::Minimum, Policy::Fixed)
                    .min_size(Size::new(140, 42)),
            )
            .widget(label("station_label", 40))
            .widget(WidgetSpec::new("combo", Size::new(247, 42), Policy::Preferred, Policy::Fixed).min_height(42))
            .widget(button("show", 137));
        let g = Layout::from(row).layout_window(Size::new(1468, 46));
        let widths: Vec<i32> =
            ["date_label", "date", "station_label", "combo", "show"].iter().map(|k| g.rect(k).w).collect();
        assert_eq!(widths, [287, 287, 288, 287, 287]);
        assert_eq!(g.rect("combo"), IRect::new(922 - 36, 2, 287, 42));
        assert_eq!(g.rect("date").h, 45);
    }

    /// The Event tab's action row: buttons at their hints, then a stretch.
    #[test]
    fn stretch_takes_the_rest() {
        let row = BoxLayout::h([0; 4], 8)
            .widget(button("a", 77))
            .widget(button("b", 107))
            .widget(button("c", 133))
            .stretch(1);
        let g = Layout::from(row).layout_window(Size::new(1162, 46));
        assert_eq!(g.rect("a"), IRect::new(0, 0, 77, 46));
        assert_eq!(g.rect("b"), IRect::new(85, 0, 107, 46));
        assert_eq!(g.rect("c"), IRect::new(200, 0, 133, 46));
    }

    /// The preview window's page: a word-wrapped caption and a canvas.
    #[test]
    fn hint_bound_items_keep_their_hints() {
        let page = BoxLayout::v([0; 4], 4).widget(label("caption", 179).hfw(std::rc::Rc::new(|_| 20))).widget(
            WidgetSpec::new("canvas", Size::new(640, 480), Policy::Preferred, Policy::Preferred)
                .min_hint(Size::new(10, 10)),
        );
        let g = Layout::from(page).layout_window(Size::new(874, 574));
        assert_eq!(g.rect("caption"), IRect::new(0, 0, 874, 90));
        assert_eq!(g.rect("canvas"), IRect::new(0, 94, 874, 480));
    }

    /// The Burst List filter grid: equal-height rows, an expanding column.
    /// A hidden widget takes no space and no spacing (`QWidgetItem::isEmpty`).
    #[test]
    fn hidden_widgets_take_no_space() {
        let v = BoxLayout::v([0; 4], 10)
            .widget(WidgetSpec::new("a", Size::new(10, 40), Policy::Preferred, Policy::Expanding))
            .widget(label("status", 30))
            .widget(WidgetSpec::new("bar", Size::new(89, 26), Policy::Expanding, Policy::Fixed).hidden(true));
        let g = Layout::from(v).layout_window(Size::new(100, 200));
        assert_eq!(g.rect("a"), IRect::new(0, 0, 100, 174));
        assert_eq!(g.rect("status"), IRect::new(0, 184, 100, 16));
        assert!(g.get("bar").is_none());
    }

    #[test]
    fn grid_columns_like_qt() {
        let mut grid = GridLayout::new([0; 4], 6, 6).column_stretch(3, 1);
        let labels = ["l0", "l1", "l2", "l3", "l4"];
        let widths = [60, 77, 54, 92, 68];
        for (i, (id, w)) in labels.iter().zip(widths).enumerate() {
            grid = grid.widget(label(id, w), 0, i);
        }
        let date = |id| WidgetSpec::new(id, Size::new(140, 45), Policy::Minimum, Policy::Fixed).min_height(42);
        grid = grid
            .widget(date("d0"), 1, 0)
            .widget(date("d1"), 1, 1)
            .widget(WidgetSpec::new("type", Size::new(117, 42), Policy::Preferred, Policy::Fixed).min_height(42), 1, 2)
            .widget(
                WidgetSpec::new("station", Size::new(128, 42), Policy::Expanding, Policy::Fixed)
                    .min_hint(Size::new(50, 42)),
                1,
                3,
            )
            .widget(WidgetSpec::new("min", Size::new(118, 45), Policy::Minimum, Policy::Fixed).min_height(42), 1, 4)
            .widget(button("load", 93), 1, 5)
            .widget_span(
                WidgetSpec::new("text", Size::new(128, 42), Policy::Expanding, Policy::Fixed)
                    .min_hint(Size::new(50, 42)),
                2,
                0,
                1,
                5,
            )
            .widget(button("reset", 92), 2, 5);
        let g = Layout::from(grid).layout_window(Size::new(1474, 16 + 6 + 46 + 6 + 46));
        let w: Vec<i32> = ["d0", "d1", "type", "station", "min", "load"].iter().map(|k| g.rect(k).w).collect();
        assert_eq!(w, [140, 140, 117, 836, 118, 93]);
        assert_eq!(g.rect("station").x, 448 - 33);
        assert_eq!(g.rect("text").w, 1375);
        assert_eq!(g.rect("reset").w, 93);
    }
}
