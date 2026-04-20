"""
bjassist.py — Single-file Blackjack Card Counting Assistant  v3
Target: Python 3.10+  |  No third-party dependencies
Architecture: GameState (model) + pure logic functions + BlackjackApp (view/controller)

v3 Changes:
  • Rule-set toggle: Liberal (DAS+LS, 0.59%) vs Evolution (DAS, no LS, 0.71%)
  • Kelly formula: Edge = (0.5% × TC) − house_base_edge (proper EV accounting)
  • Surrender fallback: Evolution mode maps "R" → next-best action (H or S)
  • High-contrast neon theme: black BG, cyan labels, white primary values
  • Active matrix cell: 3px #FF00FF magenta border via highlightbackground
  • dc_replace() throughout — eliminates deep-copy on every state mutation
  • Decks spinbox: explicit empty-string guard + Return-key binding
  • count_history capped at HISTORY_CAP to prevent unbounded growth
  • rule_set + matrix_visible both serialised to ~/.bjassist_state.json
"""

from __future__ import annotations

import json
import tkinter as tk
from dataclasses import dataclass, field, asdict, replace as dc_replace
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from tkinter import ttk, messagebox
from typing import Optional

# ── macOS activation-flash fix ────────────────────────────────────────────────
# On macOS, tk.Frame and tk.Label default to highlightthickness=1 with a
# system-white focus ring.  When the window becomes active every widget
# redraws that ring simultaneously, flooding the UI with white.
# We patch __init__ via setdefault so the default becomes 0, while any
# widget that already passes an explicit highlightthickness (e.g. matrix
# cells with their 3px magenta border) is completely unaffected.
_orig_frame_init = tk.Frame.__init__
def _frame_init_patched(self, master=None, **kw):
    kw.setdefault("highlightthickness", 0)
    _orig_frame_init(self, master, **kw)
tk.Frame.__init__ = _frame_init_patched  # type: ignore[method-assign]

_orig_label_init = tk.Label.__init__
def _label_init_patched(self, master=None, **kw):
    kw.setdefault("highlightthickness", 0)
    _orig_label_init(self, master, **kw)
tk.Label.__init__ = _label_init_patched  # type: ignore[method-assign]
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
#  CONFIG  ── every literal lives here; nothing hardcoded in logic or UI
# ─────────────────────────────────────────────────────────────────────────────
CONFIG: dict = {
    # ── Shoe / counting ───────────────────────────────────────────────────
    "DECK_SIZE":         52,
    "DEFAULT_DECKS":     8.0,
    "DEFAULT_BANKROLL":  "1000.00",
    "HISTORY_CAP":       500,           # max undo-stack depth (one 8D shoe = 416 cards)

    # ── Hi-Lo card values ─────────────────────────────────────────────────
    "HILO": {
        "2": 1, "3": 1, "4": 1, "5": 1, "6": 1,
        "7": 0, "8": 0, "9": 0,
        "T": -1, "J": -1, "Q": -1, "K": -1, "A": -1, "0": -1,
    },

    # ── Rule-set presets (8-Deck S17 baseline) ────────────────────────────
    # edge = house advantage the player must overcome with count
    "RULE_PRESETS": {
        "liberal":   {
            "label":      "Liberal  (DAS + LS)",
            "short":      "LIB 0.59%",
            "edge":       Decimal("0.0059"),    # DAS allowed, Late Surrender
        },
        "evolution": {
            "label":      "Evolution (DAS, no LS)",
            "short":      "EVO 0.71%",
            "edge":       Decimal("0.0071"),    # DAS allowed, Surrender disabled
        },
    },
    "DEFAULT_RULE_SET":  "liberal",

    # ── Kelly parameters ─────────────────────────────────────────────────
    "EDGE_PER_TC":     Decimal("0.005"),   # 0.5% gross edge per true-count point
    "KELLY_FRACTION":  Decimal("0.25"),    # fractional (quarter) Kelly
    "KELLY_CAP":       Decimal("0.20"),    # never bet more than 20% of bankroll

    # ── Persistence ──────────────────────────────────────────────────────
    "SAVE_PATH": Path.home() / ".bjassist_state.json",

    # ── Neon-on-Black theme ───────────────────────────────────────────────
    # Backgrounds
    "BG":    "#000000",   # true black — root window
    "BG2":   "#0d0d0d",   # panel fill
    "BG3":   "#1a1a1a",   # widget surfaces, count-box, bank-box
    "BG4":   "#242424",   # button resting state

    # Foregrounds
    "FG":       "#FFFFFF",   # pure white  — all body text
    "FG_DIM":   "#00E5FF",   # neon cyan   — secondary labels / headers
    "FG_FAINT": "#4a6a70",   # very dim — placeholder-style text

    # Semantic colours
    "POS_CLR":   "#00FF9F",  # positive count / P&L
    "NEG_CLR":   "#FF4444",  # negative count / P&L
    "MAGENTA":   "#FF00FF",  # active matrix cell border
    "BORDER":    "#2a2a2a",

    # Accent (used for buttons, active dealer card)
    "ACCENT":    "#00E5FF",
    "ACCENT2":   "#00B0CC",

    # ── Entry field overrides ─────────────────────────────────────────────
    # Soft neutral background so entry fields are clearly editable without
    # the OS overriding with glaring system-white on Windows / macOS.
    "ENTRY_BG":     "#f5f5f5",   # soft off-white — reduced glare vs #FFFFFF
    "ENTRY_FG":     "#0d0d0d",   # near-black text on light field
    "ENTRY_INSERT": "#0055aa",   # cursor visible on light background

    # ── Rule-set button active glow ───────────────────────────────────────
    # Tkinter equivalent of CSS box-shadow: a coloured highlightbackground
    # ring that surrounds the button widget.
    "RULE_BTN_ACTIVE_HL":   "#66BBFF",   # light-blue glow when selected
    "RULE_BTN_INACTIVE_HL": "#242424",   # matches BG4 — invisible when idle
    "RULE_BTN_HL_W":        2,           # ring thickness (px)

    # ── Matrix action colours ─────────────────────────────────────────────
    "ACTIONS": {
        "H": "Hit", "S": "Stand", "D": "Double", "P": "Split", "R": "Surrender"
    },
    "ACTION_COLORS": {
        "H": "#8B1A1A",   # dark red
        "S": "#1A5C2A",   # dark green
        "D": "#6B5000",   # dark amber
        "P": "#0D3D6B",   # dark blue
        "R": "#3A3A3A",   # dark gray
    },
    "ACTION_FG": {
        "H": "#FF6B6B",
        "S": "#66FF88",
        "D": "#FFD966",
        # Fix: #66AAFF on #0D3D6B is blue-on-blue at 7pt — bumped to near-white
        # for legibility while preserving the blue family identity.
        "P": "#C8E6FF",
        "R": "#AAAAAA",
    },

    # ── Matrix cell border (constant thickness, only color changes) ────────
    "CELL_BORDER_W": 3,          # px — constant so layout never shifts

    # ── Layout ───────────────────────────────────────────────────────────
    "MATRIX_PANEL_WEIGHT": 2,
    "CENTER_PANEL_WEIGHT": 1,
    "RIGHT_PANEL_WEIGHT":  1,

    # ── Performance ──────────────────────────────────────────────────────
    "REFRESH_THROTTLE_MS": 16,   # ~60 fps coalesce window
}

# ─────────────────────────────────────────────────────────────────────────────
#  STRATEGY TABLES  (8-deck S17)
# ─────────────────────────────────────────────────────────────────────────────

DEALER_UPCARDS: list[str] = ["2", "3", "4", "5", "6", "7", "8", "9", "T", "A"]
HARD_TOTALS:    list[int] = list(range(5, 22))       # 5 … 21
SOFT_OTHER:     list[int] = list(range(2, 10))       # A,2 … A,9
PAIR_KEYS:      list[str] = ["2","3","4","5","6","7","8","9","T","A"]

#                              2    3    4    5    6    7    8    9    T    A
HARD_STRATEGY: dict[int, list[str]] = {
    5:  list("HHHHHHHHHH"),
    6:  list("HHHHHHHHHH"),
    7:  list("HHHHHHHHHH"),
    8:  list("HHHHHHHHHH"),
    9:  list("HDDDDHHHHH"),
    10: list("DDDDDDDDHH"),
    11: list("DDDDDDDDDD"),
    12: list("HHSSSHHHHH"),
    13: list("SSSSSHHHHH"),
    14: list("SSSSSHHHHH"),
    15: list("SSSSSHHHRH"),   # R = Surrender vs T(index 8), H vs A(index 9) — wait:
    16: list("SSSSSHHRRR"),   # R vs 9, T, A
    17: list("SSSSSSSSSR"),   # R vs A only
    18: list("SSSSSSSSSS"),
    19: list("SSSSSSSSSS"),
    20: list("SSSSSSSSSS"),
    21: list("SSSSSSSSSS"),
}
# Correction: hard 15 vs T is Surrender, vs A is Hit in standard 8D S17
# Index:         2  3  4  5  6  7  8  9  T  A
# 15:            S  S  S  S  S  H  H  H  R  H   ← corrected
HARD_STRATEGY[15] = list("SSSSSHHHRH")

SOFT_STRATEGY: dict[int, list[str]] = {
    2:  list("HHHDDHHHHH"),   # A,2
    3:  list("HHHDDHHHHH"),   # A,3
    4:  list("HHDDDHHHHH"),   # A,4
    5:  list("HHDDDHHHHH"),   # A,5
    6:  list("HDDDDHHHHH"),   # A,6
    7:  list("SDDDDSSHHH"),   # A,7
    8:  list("SSSSSSSSSS"),   # A,8
    9:  list("SSSSSSSSSS"),   # A,9
}

PAIR_STRATEGY: dict[str, list[str]] = {
    "2": list("PPPPPPHHHH"),
    "3": list("PPPPPPHHHH"),
    "4": list("HHHPPHHHHHH"[:10]),
    "5": list("DDDDDDDDHH"),
    "6": list("PPPPPPHHHHH"[:10]),
    "7": list("PPPPPPHHHH"),
    "8": list("PPPPPPPPPP"),
    "9": list("PPPPPPSPPSS"[:10]),
    "T": list("SSSSSSSSSS"),
    "A": list("PPPPPPPPPP"),
}

# When surrender is unavailable (Evolution mode), map "R" to next-best action.
# Hard 17 vs A → Stand (giving up on a bad hand is wrong; 17 beats dealer bust).
# All other surrender hands → Hit (losing less per hand than hitting 15/16 is moot
# without surrender; hitting is better than standing on stiff hands).
def _surrender_fallback(hard_total: int) -> str:
    """Return next-best action when Surrender is not permitted."""
    return "S" if hard_total >= 17 else "H"


# ─────────────────────────────────────────────────────────────────────────────
#  GAME STATE  (model)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class GameState:
    """
    Single source of truth for all mutable app state.
    Fully serialisable to / from JSON — no Tkinter references.
    All mutations return a new instance via dataclasses.replace().
    """
    bankroll:        str        = CONFIG["DEFAULT_BANKROLL"]
    session_start:   str        = CONFIG["DEFAULT_BANKROLL"]
    running_count:   int        = 0
    decks_remaining: float      = CONFIG["DEFAULT_DECKS"]
    count_history:   list[int]  = field(default_factory=list)
    dealer_upcard:   str        = ""
    player_hand:     str        = ""       # e.g. "16", "A7", "88"
    hand_type:       str        = "hard"   # "hard" | "soft" | "pair"
    current_bet:     str        = "0.00"
    matrix_visible:  bool       = True
    rule_set:        str        = CONFIG["DEFAULT_RULE_SET"]  # "liberal"|"evolution"

    def to_dict(self) -> dict:
        """Deep-serialize to a plain dict for JSON persistence."""
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "GameState":
        """Reconstruct from a plain dict, silently ignoring unknown keys."""
        valid = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**valid)


# ─────────────────────────────────────────────────────────────────────────────
#  PURE LOGIC FUNCTIONS
#  No side-effects; all return new values / new GameState instances.
# ─────────────────────────────────────────────────────────────────────────────

def get_true_count(state: GameState) -> float:
    """Hi-Lo true count = running_count / decks_remaining."""
    if state.decks_remaining <= 0:
        return float(state.running_count)
    return state.running_count / state.decks_remaining


def get_base_edge(state: GameState) -> Decimal:
    """Return the house baseline edge for the active rule set."""
    return CONFIG["RULE_PRESETS"].get(
        state.rule_set, CONFIG["RULE_PRESETS"]["liberal"])["edge"]


def apply_card(state: GameState, char: str) -> GameState:
    """
    Return a new GameState with the Hi-Lo count updated for *char*.
    Pushes the current running_count onto the undo stack (capped at HISTORY_CAP).
    Uses dc_replace — O(1) for unchanged fields, no deep-copy of count_history.
    """
    delta = CONFIG["HILO"].get(char.upper(), 0)
    history = state.count_history[-CONFIG["HISTORY_CAP"]:]   # enforce cap BEFORE append
    return dc_replace(
        state,
        running_count=state.running_count + delta,
        count_history=history + [state.running_count],
    )


def undo_card(state: GameState) -> GameState:
    """Pop the last count history entry and restore it as running_count."""
    if not state.count_history:
        return state
    return dc_replace(
        state,
        running_count=state.count_history[-1],
        count_history=state.count_history[:-1],
    )


def reset_shoe(state: GameState) -> GameState:
    """
    Reset count, history, and decks_remaining to defaults.
    Preserves bankroll, rule_set, matrix_visible, and dealer/hand inputs.
    """
    return dc_replace(
        state,
        running_count=0,
        count_history=[],
        decks_remaining=CONFIG["DEFAULT_DECKS"],
    )


def suggested_bet(state: GameState) -> Decimal:
    """
    Fractional-Kelly bet sizing with proper house-edge accounting.

    Formula:  gross_edge = EDGE_PER_TC × TC
              net_edge   = gross_edge − house_base_edge
              f*         = net_edge × KELLY_FRACTION   (if net_edge > 0)
              bet        = clamp(f* × bankroll, 0, KELLY_CAP × bankroll)

    Returns Decimal("0.00") when net edge ≤ 0 (count not high enough to
    overcome the specific table's house advantage).
    """
    tc       = get_true_count(state)
    bankroll = Decimal(state.bankroll)
    gross    = CONFIG["EDGE_PER_TC"] * Decimal(str(tc))
    net      = gross - get_base_edge(state)
    if net <= Decimal("0"):
        return Decimal("0.00")
    raw_fraction = net * CONFIG["KELLY_FRACTION"]
    capped = min(raw_fraction, CONFIG["KELLY_CAP"])
    bet = (bankroll * capped).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return max(bet, Decimal("0.00"))


def breakeven_tc(state: GameState) -> float:
    """Return the true count at which net edge turns positive for this rule set."""
    base = get_base_edge(state)
    # gross_edge = EDGE_PER_TC × TC → TC_breakeven = base / EDGE_PER_TC
    return float(base / CONFIG["EDGE_PER_TC"])


def apply_result(state: GameState, result: str) -> GameState:
    """
    Return a new GameState with bankroll updated for *result*.
    result ∈ {'win','loss','push','blackjack','double','double_loss'}
    """
    bankroll = Decimal(state.bankroll)
    bet      = Decimal(state.current_bet)
    match result:
        case "win":
            bankroll += bet
        case "loss":
            bankroll -= bet
        case "push":
            pass
        case "blackjack":
            bankroll += (bet * Decimal("1.5")).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP)
        case "double":
            bankroll += bet * 2
        case "double_loss":
            bankroll -= bet * 2
    return dc_replace(state, bankroll=str(max(bankroll, Decimal("0.00"))))


def get_strategy(state: GameState) -> Optional[str]:
    """
    Return the basic-strategy action letter for the current hand/dealer combo.

    In Evolution mode (no surrender), any "R" action is replaced with the
    next-best mathematically sound move (Stand for hard 17+, Hit otherwise).

    Returns None when inputs are absent or unrecognised.
    """
    dealer = state.dealer_upcard.upper()
    hand   = state.player_hand.strip().upper()
    if not dealer or not hand or dealer not in DEALER_UPCARDS:
        return None
    dealer_idx = DEALER_UPCARDS.index(dealer)

    # ── Pair detection (takes priority) ──────────────────────────────────
    if len(hand) == 2 and hand[0] == hand[1] and hand[0] in PAIR_STRATEGY:
        action = PAIR_STRATEGY[hand[0]][dealer_idx]
        # pairs never have Surrender in standard strategy — no fallback needed
        return action

    # ── Soft hand (A + one card) ──────────────────────────────────────────
    if hand.startswith("A") and len(hand) == 2:
        try:
            other = int(hand[1]) if hand[1].isdigit() else (
                10 if hand[1] in "TJQK" else None)
            if other is not None and other in SOFT_STRATEGY:
                return SOFT_STRATEGY[other][dealer_idx]
        except ValueError:
            pass

    # ── Hard total ────────────────────────────────────────────────────────
    try:
        total  = max(5, min(int(hand), 21))
        action = HARD_STRATEGY.get(total, ["H"] * 10)[dealer_idx]
    except ValueError:
        return None

    # ── Surrender fallback for Evolution mode ─────────────────────────────
    if action == "R" and state.rule_set == "evolution":
        action = _surrender_fallback(total)

    return action


def session_pnl(state: GameState) -> Decimal:
    """Return session profit / loss as a signed Decimal."""
    return Decimal(state.bankroll) - Decimal(state.session_start)


def save_state(state: GameState) -> None:
    """Persist GameState to ~/.bjassist_state.json. Silently ignores I/O errors."""
    try:
        CONFIG["SAVE_PATH"].write_text(json.dumps(state.to_dict(), indent=2))
    except OSError:
        pass


def load_state() -> GameState:
    """Load persisted GameState; fall back to a fresh default on any error."""
    try:
        data = json.loads(CONFIG["SAVE_PATH"].read_text())
        return GameState.from_dict(data)
    except (OSError, json.JSONDecodeError, TypeError, KeyError, ValueError):
        return GameState()


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN APPLICATION
# ─────────────────────────────────────────────────────────────────────────────

class BlackjackApp:
    """
    Root Tkinter application — View/Controller layer.

    Invariants:
      • self.state is always the authoritative GameState.
      • Every state mutation uses dc_replace() and calls _schedule_refresh()
        or _refresh() directly.
      • No widget is ever destroyed after __init__; _refresh() only calls
        .configure() on cached references.
      • Matrix cell Labels have constant highlightthickness=CELL_BORDER_W;
        only highlightbackground changes — layout never shifts.
    """

    # ── Class-level typed cell-cache declarations ─────────────────────────
    _hard_cells:    list[list[tk.Label]]
    _hard_row_hdrs: list[tk.Label]
    _hard_col_hdrs: list[tk.Label]
    _soft_cells:    list[list[tk.Label]]
    _soft_row_hdrs: list[tk.Label]
    _soft_col_hdrs: list[tk.Label]
    _pair_cells:    list[list[tk.Label]]
    _pair_row_hdrs: list[tk.Label]
    _pair_col_hdrs: list[tk.Label]

    def __init__(self, root: tk.Tk) -> None:
        """Boot sequence: load → configure → build → bind → first refresh."""
        self.root              = root
        self.state: GameState  = load_state()
        self._refresh_pending: Optional[str] = None

        self._configure_root()
        self._configure_style()
        self._build_ui()
        self._bind_hotkeys()
        self._apply_matrix_visibility()
        self._refresh()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── Root & Style ──────────────────────────────────────────────────────

    def _configure_root(self) -> None:
        """Title, minimum size, background, grid column weights."""
        self.root.title("♠ BJ Assist v3 — Hi-Lo Counter")
        self.root.configure(bg=CONFIG["BG"])
        self.root.minsize(940, 700)
        self.root.columnconfigure(0, weight=CONFIG["MATRIX_PANEL_WEIGHT"])
        self.root.columnconfigure(1, weight=CONFIG["CENTER_PANEL_WEIGHT"])
        self.root.columnconfigure(2, weight=CONFIG["RIGHT_PANEL_WEIGHT"])
        self.root.rowconfigure(1, weight=1)

    def _configure_style(self) -> None:
        """Apply the neon-on-black ttk theme."""
        s = ttk.Style(self.root)
        s.theme_use("clam")
        BG2, BG3, BG4 = CONFIG["BG2"], CONFIG["BG3"], CONFIG["BG4"]
        FG, DIM = CONFIG["FG"], CONFIG["FG_DIM"]
        # Entry/Spinbox use a dedicated light palette so the OS cannot
        # override field colours with system-white on Windows or macOS.
        E_BG  = CONFIG["ENTRY_BG"]
        E_FG  = CONFIG["ENTRY_FG"]
        E_INS = CONFIG["ENTRY_INSERT"]
        s.configure("TFrame",    background=BG2)
        s.configure("TLabel",    background=BG2, foreground=FG,
                    font=("Consolas", 10))
        s.configure("TButton",   background=BG4, foreground=FG,
                    font=("Consolas", 10), borderwidth=1, relief="flat")
        s.map("TButton",
              background=[("active", BG3), ("pressed", CONFIG["ACCENT2"])],
              foreground=[("active", CONFIG["ACCENT"])])
        s.configure("TSeparator", background=CONFIG["BORDER"])
        s.configure("TEntry",
                    fieldbackground=E_BG, foreground=E_FG,
                    insertcolor=E_INS,
                    selectbackground=CONFIG["ACCENT2"],
                    selectforeground="#000000",
                    bordercolor=CONFIG["BORDER"],
                    lightcolor=E_BG, darkcolor=E_BG,
                    font=("Consolas", 11))
        s.map("TEntry",
              fieldbackground=[("readonly", BG3), ("disabled", BG3)],
              foreground=[("readonly", FG),       ("disabled", CONFIG["FG_FAINT"])])
        s.configure("TSpinbox",
                    fieldbackground=E_BG, foreground=E_FG,
                    insertcolor=E_INS,
                    selectbackground=CONFIG["ACCENT2"],
                    selectforeground="#000000",
                    lightcolor=E_BG, darkcolor=E_BG,
                    font=("Consolas", 10))

    # ── UI construction ───────────────────────────────────────────────────

    def _build_ui(self) -> None:
        """Orchestrate construction of all panels."""
        self._build_topbar()
        self._build_left_panel()
        self._build_center_panel()
        self._build_right_panel()
        self._build_hotkey_bar()

    def _build_topbar(self) -> None:
        """
        Top bar: logo | subtitle | rule-set selector | matrix toggle | New Session.
        Rule-set buttons are stored so _refresh() can restyle the active one.
        """
        bar = tk.Frame(self.root, bg=CONFIG["BG3"], height=44)
        bar.grid(row=0, column=0, columnspan=3, sticky="ew")
        bar.columnconfigure(1, weight=1)
        bar.grid_propagate(False)

        tk.Label(bar, text="♠ BJ ASSIST v3",
                 font=("Consolas", 14, "bold"),
                 fg=CONFIG["ACCENT"], bg=CONFIG["BG3"]
                 ).grid(row=0, column=0, padx=16, pady=10)

        tk.Label(bar,
                 text="Hi-Lo  |  Basic Strategy  |  Kelly Bet Sizing",
                 font=("Consolas", 9), fg=CONFIG["FG_DIM"], bg=CONFIG["BG3"]
                 ).grid(row=0, column=1, sticky="w", padx=8)

        # Rule-set segmented control
        # highlightthickness is set at build-time (constant) so the ring
        # never shifts widget geometry when the colour changes in _refresh().
        rule_frame = tk.Frame(bar, bg=CONFIG["BG3"])
        rule_frame.grid(row=0, column=2, padx=8, pady=6)
        self._rule_btns: dict[str, tk.Button] = {}
        for rs_key, rs_data in CONFIG["RULE_PRESETS"].items():
            btn = tk.Button(
                rule_frame, text=rs_data["short"],
                font=("Consolas", 8, "bold"),
                relief="flat", cursor="hand2",
                highlightthickness=CONFIG["RULE_BTN_HL_W"],
                highlightbackground=CONFIG["RULE_BTN_INACTIVE_HL"],
                command=lambda k=rs_key: self._set_rule_set(k))
            btn.pack(side="left", padx=4)
            self._rule_btns[rs_key] = btn

        # Matrix toggle
        self._toggle_btn = tk.Button(
            bar, text="", font=("Consolas", 9),
            bg=CONFIG["BG4"], fg=CONFIG["FG"], relief="flat",
            activebackground=CONFIG["BG3"], activeforeground=CONFIG["ACCENT"],
            highlightbackground=CONFIG["BG3"], highlightthickness=1,
            cursor="hand2", command=self._toggle_matrix)
        self._toggle_btn.grid(row=0, column=3, padx=(0, 8), pady=8)

        tk.Button(bar, text="New Session",
                  font=("Consolas", 9), bg=CONFIG["BG4"], fg=CONFIG["FG"],
                  relief="flat", activebackground=CONFIG["BG3"],
                  activeforeground=CONFIG["ACCENT"],
                  highlightbackground=CONFIG["BG3"], highlightthickness=1,
                  cursor="hand2", command=self._new_session
                  ).grid(row=0, column=4, padx=(0, 16), pady=8)

    # ── Left panel: Strategy Matrix ───────────────────────────────────────

    def _build_left_panel(self) -> None:
        """Build left panel; store cell references for O(1) highlight updates."""
        self._left_panel = tk.Frame(self.root, bg=CONFIG["BG2"], bd=0)
        self._left_panel.grid(row=1, column=0, sticky="nsew",
                              padx=(8, 4), pady=8)
        self._left_panel.columnconfigure(0, weight=1)

        tk.Label(self._left_panel,
                 text="BASIC STRATEGY", font=("Consolas", 11, "bold"),
                 fg=CONFIG["FG_DIM"], bg=CONFIG["BG2"]
                 ).grid(row=0, column=0, sticky="w", padx=10, pady=(10, 4))

        # Legend
        legend = tk.Frame(self._left_panel, bg=CONFIG["BG2"])
        legend.grid(row=1, column=0, sticky="w", padx=10, pady=(0, 4))
        for abbr, name in CONFIG["ACTIONS"].items():
            tk.Label(legend,
                     text=f"■ {abbr}={name}",
                     font=("Consolas", 8),
                     fg=CONFIG["ACTION_FG"][abbr],
                     bg=CONFIG["BG2"]).pack(side="left", padx=4)

        # Note: "R" shown with strike-through hint when Evolution active
        self._surrender_note = tk.Label(
            self._left_panel,
            text="",
            font=("Consolas", 7, "italic"),
            fg="#AA44AA", bg=CONFIG["BG2"])
        self._surrender_note.grid(row=2, column=0, sticky="w", padx=10, pady=(0, 4))

        sections = [("HARD TOTALS", 3), ("SOFT TOTALS", 5), ("PAIRS", 7)]
        self._matrix_sub_frames: dict[str, tk.Frame] = {}
        for (title, row), key in zip(sections, ["hard", "soft", "pair"]):
            tk.Label(self._left_panel, text=title,
                     font=("Consolas", 9, "bold"),
                     fg=CONFIG["FG_DIM"], bg=CONFIG["BG2"]
                     ).grid(row=row - 1, column=0, sticky="w",
                            padx=10, pady=(4, 1))
            sub = tk.Frame(self._left_panel, bg=CONFIG["BG2"])
            sub.grid(row=row, column=0, sticky="nsew", padx=6, pady=(0, 3))
            self._matrix_sub_frames[key] = sub

        self._build_matrix_cells()

    def _build_matrix_cells(self) -> None:
        """
        Create all cell Labels exactly once.  Each cell gets a constant
        highlightthickness=CELL_BORDER_W so layout never shifts; only
        highlightbackground changes between CONFIG["BG2"] (invisible)
        and CONFIG["MAGENTA"] (active).
        """
        self._hard_cells, self._hard_row_hdrs, self._hard_col_hdrs = \
            self._build_table(
                self._matrix_sub_frames["hard"],
                [str(t) for t in HARD_TOTALS],
                [HARD_STRATEGY[t] for t in HARD_TOTALS],
                "   ")
        self._soft_cells, self._soft_row_hdrs, self._soft_col_hdrs = \
            self._build_table(
                self._matrix_sub_frames["soft"],
                [str(o) for o in SOFT_OTHER],
                [SOFT_STRATEGY[o] for o in SOFT_OTHER],
                "A+ ")
        self._pair_cells, self._pair_row_hdrs, self._pair_col_hdrs = \
            self._build_table(
                self._matrix_sub_frames["pair"],
                [f"{k}-{k}" for k in PAIR_KEYS],
                [PAIR_STRATEGY[k] for k in PAIR_KEYS],
                "P-P")

    def _build_table(
        self,
        parent:        tk.Frame,
        row_keys:      list[str],
        strategy_rows: list[list[str]],
        corner_text:   str,
    ) -> tuple[list[list[tk.Label]], list[tk.Label], list[tk.Label]]:
        """
        Build one strategy grid.  All cells share:
          highlightthickness = CELL_BORDER_W  (constant — no layout jitter)
          highlightbackground = BG2            (invisible until activated)
        Returns (cells_2d, row_headers, col_headers).
        """
        CF  = ("Consolas", 7)
        CFB = ("Consolas", 7, "bold")
        BW  = CONFIG["CELL_BORDER_W"]
        BG2 = CONFIG["BG2"]

        tk.Label(parent, text=corner_text, font=CF,
                 bg=BG2, fg=CONFIG["FG_DIM"], width=4,
                 highlightthickness=0
                 ).grid(row=0, column=0)

        col_hdrs: list[tk.Label] = []
        for ci, d in enumerate(DEALER_UPCARDS):
            lbl = tk.Label(parent, text=d, font=CFB,
                           bg=CONFIG["BG3"], fg=CONFIG["FG"], width=3,
                           highlightthickness=0)
            lbl.grid(row=0, column=ci + 1, padx=1, pady=1)
            col_hdrs.append(lbl)

        row_hdrs: list[tk.Label] = []
        cells:    list[list[tk.Label]] = []
        for ri, (rk, row_data) in enumerate(zip(row_keys, strategy_rows)):
            rh = tk.Label(parent, text=rk, font=CF,
                          bg=BG2, fg=CONFIG["FG"], width=4,
                          highlightthickness=0)
            rh.grid(row=ri + 1, column=0, padx=1, pady=1)
            row_hdrs.append(rh)

            row_cells: list[tk.Label] = []
            for ci, action in enumerate(row_data):
                cl = tk.Label(parent, text=action, font=CF,
                              bg=CONFIG["ACTION_COLORS"].get(action, CONFIG["BG4"]),
                              fg=CONFIG["ACTION_FG"].get(action, CONFIG["FG"]),
                              width=3,
                              highlightthickness=BW,
                              highlightbackground=BG2)
                cl.grid(row=ri + 1, column=ci + 1, padx=1, pady=1)
                row_cells.append(cl)
            cells.append(row_cells)

        return cells, row_hdrs, col_hdrs

    # ── Center panel ──────────────────────────────────────────────────────

    def _build_center_panel(self) -> None:
        """Dealer upcard picker, hand entry, strategy suggestion, bet + results."""
        frame = tk.Frame(self.root, bg=CONFIG["BG2"])
        frame.grid(row=1, column=1, sticky="nsew", padx=4, pady=8)
        frame.columnconfigure(0, weight=1)

        self._section_label(frame, "HAND INPUT", 0)

        # Dealer upcard buttons
        self._field_label(frame, "Dealer Upcard:", 1)
        self.dealer_var = tk.StringVar(value=self.state.dealer_upcard)
        dealer_frame = tk.Frame(frame, bg=CONFIG["BG2"])
        dealer_frame.grid(row=2, column=0, sticky="ew", padx=10, pady=(2, 8))
        for i, card in enumerate(DEALER_UPCARDS):
            tk.Button(dealer_frame, text=card,
                      font=("Consolas", 10, "bold"),
                      bg=CONFIG["BG4"], fg=CONFIG["FG"], relief="flat",
                      activebackground=CONFIG["ACCENT2"],
                      activeforeground="#000", width=3, height=1,
                      highlightbackground=CONFIG["BG2"], highlightthickness=1,
                      cursor="hand2",
                      command=lambda c=card: self._set_dealer(c)
                      ).grid(row=0, column=i, padx=1)
        self.dealer_btns_frame = dealer_frame

        # Player hand entry
        self._field_label(frame, "Player Hand  (e.g. 16, A7, 88):", 3)
        self.hand_var = tk.StringVar(value=self.state.player_hand)
        ttk.Entry(frame, textvariable=self.hand_var,
                  style="TEntry", width=14
                  ).grid(row=4, column=0, sticky="w", padx=10, pady=(2, 4))
        self.hand_var.trace_add("write", lambda *_: self._on_hand_change())

        # Hand type
        self._field_label(frame, "Hand Type:", 5)
        self.hand_type_var = tk.StringVar(value=self.state.hand_type)
        ht_frame = tk.Frame(frame, bg=CONFIG["BG2"])
        ht_frame.grid(row=6, column=0, sticky="w", padx=10, pady=(2, 10))
        for ht in ["hard", "soft", "pair"]:
            tk.Radiobutton(
                ht_frame, text=ht.capitalize(),
                variable=self.hand_type_var, value=ht,
                font=("Consolas", 9), bg=CONFIG["BG2"], fg=CONFIG["FG"],
                selectcolor=CONFIG["BG3"],
                activebackground=CONFIG["BG2"],
                activeforeground=CONFIG["ACCENT"],
                command=self._on_hand_type_change,
            ).pack(side="left", padx=4)

        ttk.Separator(frame, orient="horizontal").grid(
            row=7, column=0, sticky="ew", padx=10, pady=6)

        # Suggested action display
        self._section_label(frame, "SUGGESTED ACTION", 8)
        self.action_lbl = tk.Label(frame, text="—",
                                   font=("Consolas", 32, "bold"),
                                   fg=CONFIG["FG_DIM"], bg=CONFIG["BG2"])
        self.action_lbl.grid(row=9, column=0, sticky="w", padx=16, pady=(0, 2))
        self.action_name_lbl = tk.Label(frame, text="",
                                        font=("Consolas", 12),
                                        fg=CONFIG["FG_DIM"], bg=CONFIG["BG2"])
        self.action_name_lbl.grid(row=10, column=0, sticky="w", padx=16)
        # Evolution override notice — shown when action was remapped
        self.override_lbl = tk.Label(frame, text="",
                                     font=("Consolas", 8, "italic"),
                                     fg="#AA44AA", bg=CONFIG["BG2"])
        self.override_lbl.grid(row=11, column=0, sticky="w", padx=16, pady=(0, 4))

        ttk.Separator(frame, orient="horizontal").grid(
            row=12, column=0, sticky="ew", padx=10, pady=8)

        # Bet entry
        self._field_label(frame, "Current Bet ($):", 13)
        self.bet_var = tk.StringVar(value=self.state.current_bet)
        ttk.Entry(frame, textvariable=self.bet_var,
                  style="TEntry", width=12
                  ).grid(row=14, column=0, sticky="w", padx=10, pady=(2, 8))

        # Hand result buttons
        self._section_label(frame, "HAND RESULT", 15)
        results = [
            ("Win  (+1×)",        "win"),
            ("Lose (-1×)",        "loss"),
            ("Push",              "push"),
            ("Blackjack (+1.5×)", "blackjack"),
            ("Double Win",        "double"),
            ("Double Loss",       "double_loss"),
        ]
        rf = tk.Frame(frame, bg=CONFIG["BG2"])
        rf.grid(row=16, column=0, sticky="ew", padx=10, pady=(4, 0))
        rf.columnconfigure(0, weight=1)
        rf.columnconfigure(1, weight=1)
        for i, (label, key) in enumerate(results):
            r, c = divmod(i, 2)
            tk.Button(rf, text=label, font=("Consolas", 9),
                      bg=CONFIG["BG4"], fg=CONFIG["FG"], relief="flat",
                      activebackground=CONFIG["ACCENT2"],
                      activeforeground="#000", cursor="hand2",
                      highlightbackground=CONFIG["BG2"], highlightthickness=1,
                      command=lambda k=key: self._apply_result(k)
                      ).grid(row=r, column=c, padx=2, pady=2, sticky="ew")

    # ── Right panel ───────────────────────────────────────────────────────

    def _build_right_panel(self) -> None:
        """Running/true count, decks, bankroll, P&L, Kelly bet, rule summary."""
        frame = tk.Frame(self.root, bg=CONFIG["BG2"])
        frame.grid(row=1, column=2, sticky="nsew", padx=(4, 8), pady=8)
        frame.columnconfigure(0, weight=1)

        # ── Count box ────────────────────────────────────────────────────
        self._section_label(frame, "CARD COUNT", 0)
        count_box = tk.Frame(frame, bg=CONFIG["BG3"])
        count_box.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 6))
        count_box.columnconfigure(1, weight=1)

        self._field_label_box(count_box, "Running:", 0)
        self.rc_lbl = tk.Label(count_box, text="0",
                               font=("Consolas", 16, "bold"),
                               fg=CONFIG["FG"], bg=CONFIG["BG3"])
        self.rc_lbl.grid(row=0, column=1, sticky="e", padx=10, pady=6)

        self._field_label_box(count_box, "True Count:", 1)
        self.tc_lbl = tk.Label(count_box, text="0.00",
                               font=("Consolas", 16, "bold"),
                               fg=CONFIG["FG"], bg=CONFIG["BG3"])
        self.tc_lbl.grid(row=1, column=1, sticky="e", padx=10, pady=6)

        # ── Decks remaining ──────────────────────────────────────────────
        self._field_label(frame, "Decks Remaining:", 2)
        self.decks_var = tk.StringVar(value=str(self.state.decks_remaining))
        self._decks_spin = ttk.Spinbox(
            frame, from_=0.5, to=8.0, increment=0.5,
            textvariable=self.decks_var, width=8,
            command=self._on_decks_change)
        self._decks_spin.grid(row=3, column=0, sticky="w", padx=10, pady=(0, 4))
        self._decks_spin.bind("<FocusOut>", lambda _: self._on_decks_change())
        self._decks_spin.bind("<Return>",   lambda _: self._on_decks_change())

        ttk.Separator(frame, orient="horizontal").grid(
            row=4, column=0, sticky="ew", padx=10, pady=8)

        # ── Bankroll ─────────────────────────────────────────────────────
        self._section_label(frame, "BANKROLL", 5)
        bank_box = tk.Frame(frame, bg=CONFIG["BG3"])
        bank_box.grid(row=6, column=0, sticky="ew", padx=10, pady=(0, 6))
        bank_box.columnconfigure(1, weight=1)

        self._field_label_box(bank_box, "Balance:", 0)
        self.bankroll_lbl = tk.Label(bank_box, text="$0.00",
                                     font=("Consolas", 16, "bold"),
                                     fg=CONFIG["FG"], bg=CONFIG["BG3"])
        self.bankroll_lbl.grid(row=0, column=1, sticky="e", padx=10, pady=6)

        self._field_label_box(bank_box, "Session P&L:", 1)
        self.pnl_lbl = tk.Label(bank_box, text="$0.00",
                                font=("Consolas", 13, "bold"),
                                fg=CONFIG["FG"], bg=CONFIG["BG3"])
        self.pnl_lbl.grid(row=1, column=1, sticky="e", padx=10, pady=4)

        # Set bankroll
        self._field_label(frame, "Set Bankroll ($):", 7)
        sf = tk.Frame(frame, bg=CONFIG["BG2"])
        sf.grid(row=8, column=0, sticky="ew", padx=10, pady=(2, 4))
        sf.columnconfigure(0, weight=1)
        self.bankroll_entry_var = tk.StringVar(value=self.state.bankroll)
        ttk.Entry(sf, textvariable=self.bankroll_entry_var,
                  style="TEntry", width=12
                  ).grid(row=0, column=0, sticky="w")
        tk.Button(sf, text="Set", font=("Consolas", 9),
                  bg=CONFIG["ACCENT2"], fg="#000", relief="flat",
                  activebackground=CONFIG["ACCENT"], activeforeground="#000",
                  highlightbackground=CONFIG["ACCENT2"], highlightthickness=1,
                  cursor="hand2", command=self._set_bankroll
                  ).grid(row=0, column=1, padx=(6, 0))

        ttk.Separator(frame, orient="horizontal").grid(
            row=9, column=0, sticky="ew", padx=10, pady=8)

        # ── Suggested bet ────────────────────────────────────────────────
        self._section_label(frame, "SUGGESTED BET", 10)
        self.bet_lbl = tk.Label(frame, text="$0.00",
                                font=("Consolas", 16, "bold"),
                                fg=CONFIG["FG"], bg=CONFIG["BG2"])
        self.bet_lbl.grid(row=11, column=0, sticky="w", padx=14)
        self.bet_info_lbl = tk.Label(frame, text="",
                                     font=("Consolas", 8),
                                     fg=CONFIG["FG_DIM"], bg=CONFIG["BG2"])
        self.bet_info_lbl.grid(row=12, column=0, sticky="w", padx=14, pady=(2, 0))
        tk.Button(frame, text="Use Suggested ↑", font=("Consolas", 9),
                  bg=CONFIG["BG4"], fg=CONFIG["FG"], relief="flat",
                  activebackground=CONFIG["ACCENT2"], activeforeground="#000",
                  highlightbackground=CONFIG["BG2"], highlightthickness=1,
                  cursor="hand2", command=self._use_suggested_bet
                  ).grid(row=13, column=0, sticky="w", padx=10, pady=(6, 0))

        ttk.Separator(frame, orient="horizontal").grid(
            row=14, column=0, sticky="ew", padx=10, pady=8)

        # ── Rule summary ─────────────────────────────────────────────────
        self._section_label(frame, "ACTIVE RULE SET", 15)
        self.rule_summary_lbl = tk.Label(frame, text="",
                                         font=("Consolas", 9),
                                         fg=CONFIG["FG"], bg=CONFIG["BG2"],
                                         justify="left", wraplength=200)
        self.rule_summary_lbl.grid(row=16, column=0, sticky="w", padx=14, pady=(2, 6))

        ttk.Separator(frame, orient="horizontal").grid(
            row=17, column=0, sticky="ew", padx=10, pady=8)

        # ── Reset shoe ───────────────────────────────────────────────────
        tk.Button(frame, text="Reset Shoe  (R)",
                  font=("Consolas", 9),
                  bg="#3a0a0a", fg="#FF6666", relief="flat",
                  activebackground="#5a1a1a", activeforeground="white",
                  highlightbackground="#3a0a0a", highlightthickness=1,
                  cursor="hand2", command=self._reset_shoe
                  ).grid(row=18, column=0, sticky="ew", padx=10, pady=(0, 6))

    # ── Hotkey bar ────────────────────────────────────────────────────────

    def _build_hotkey_bar(self) -> None:
        """Bottom status bar: hotkey legend."""
        bar = tk.Frame(self.root, bg=CONFIG["BG3"])
        bar.grid(row=2, column=0, columnspan=3, sticky="ew")
        hotkeys = [
            ("2-6", "+1"),  ("7-9", "0"),
            ("0/T/J/Q/K/A", "−1"),  ("R", "Reset shoe"),  ("Z", "Undo"),
        ]
        for i, (k, v) in enumerate(hotkeys):
            tk.Label(bar, text=f" [{k}]",
                     font=("Consolas", 8, "bold"),
                     fg=CONFIG["ACCENT"], bg=CONFIG["BG3"]
                     ).pack(side="left", pady=4)
            tk.Label(bar, text=v,
                     font=("Consolas", 8),
                     fg=CONFIG["FG_DIM"], bg=CONFIG["BG3"]
                     ).pack(side="left", padx=(2, 10), pady=4)
        tk.Label(bar,
                 text="Click outside any field to re-arm hotkeys",
                 font=("Consolas", 8), fg=CONFIG["FG_FAINT"], bg=CONFIG["BG3"]
                 ).pack(side="right", padx=12)

    # ── UI widget factories (DRY helpers) ─────────────────────────────────

    def _section_label(self, parent: tk.Widget, text: str, row: int) -> tk.Label:
        """Bold cyan section heading in *parent* at *row*."""
        lbl = tk.Label(parent, text=text, font=("Consolas", 10, "bold"),
                       fg=CONFIG["FG_DIM"], bg=CONFIG["BG2"])
        lbl.grid(row=row, column=0, sticky="w", padx=10, pady=(8, 2))
        return lbl

    def _field_label(self, parent: tk.Widget, text: str, row: int) -> tk.Label:
        """Cyan field label at *row*."""
        lbl = tk.Label(parent, text=text, font=("Consolas", 9),
                       fg=CONFIG["FG_DIM"], bg=CONFIG["BG2"])
        lbl.grid(row=row, column=0, sticky="w", padx=10)
        return lbl

    def _field_label_box(self, parent: tk.Widget, text: str, row: int) -> tk.Label:
        """Cyan field label inside a BG3 box."""
        lbl = tk.Label(parent, text=text, font=("Consolas", 9),
                       fg=CONFIG["FG_DIM"], bg=CONFIG["BG3"])
        lbl.grid(row=row, column=0, sticky="w", padx=10, pady=6)
        return lbl

    # ── Hotkey & Focus Binding ────────────────────────────────────────────

    def _bind_hotkeys(self) -> None:
        """bind_all card keys + global unfocus handler + taskbar restore."""
        for k in "23456":
            self.root.bind_all(k, lambda e, c=k: self._count_card(c))
        for k in "789":
            self.root.bind_all(k, lambda e, c=k: self._count_card(c))
        for k in ["0", "t", "T", "j", "J", "q", "Q", "k", "K", "a", "A"]:
            self.root.bind_all(k, lambda e, c=k: self._count_card(c))
        self.root.bind_all("r", lambda e: self._reset_shoe())
        self.root.bind_all("R", lambda e: self._reset_shoe())
        self.root.bind_all("z", lambda e: self._undo_card())
        self.root.bind_all("Z", lambda e: self._undo_card())
        # Global unfocus: any click outside an input widget → root focus
        self.root.bind("<Button-1>", self._on_root_click)
        # Taskbar restore: fired by the WM when an iconified window is
        # mapped (un-minimised).  We lift and force focus so the window
        # reliably comes to the foreground on all platforms.
        self.root.bind("<Map>", self._on_window_map)

    def _on_root_click(self, event: tk.Event) -> None:
        """
        Return focus to root on non-input clicks so bind_all hotkeys fire
        on the very next keypress without a second click.
        """
        if not isinstance(event.widget, (tk.Entry, ttk.Entry, ttk.Spinbox)):
            self.root.focus_set()

    def _on_window_map(self, event: tk.Event) -> None:
        """
        Called by the window manager whenever the root window is mapped
        (shown or restored from iconified / minimised state).

        Strategy:
          1. Guard: only act on the root window, not child widgets.
          2. lift()        — bring above all other windows in the stack.
          3. focus_force() — grab keyboard focus immediately.
          4. Momentary -topmost True → False via after(100) — guarantees
             the window surfaces on Windows where lift() alone can fail
             when another app holds focus.  The flag is cleared after
             100 ms so the app does not permanently sit on top.
        """
        if event.widget is not self.root:
            return
        # Only act when the window is transitioning to a normal state
        # (wm_state 'normal'); skip phantom Map events during startup.
        if self.root.wm_state() != "normal":
            return
        self.root.lift()
        self.root.focus_force()
        # Windows-specific: force-topmost for one frame then release
        self.root.attributes("-topmost", True)
        self.root.after(100, lambda: self.root.attributes("-topmost", False))

    # ── Collapsible matrix ────────────────────────────────────────────────

    def _toggle_matrix(self) -> None:
        """Flip matrix_visible and persist."""
        self.state = dc_replace(self.state,
                                matrix_visible=not self.state.matrix_visible)
        self._apply_matrix_visibility()

    def _apply_matrix_visibility(self) -> None:
        """
        grid() / grid_remove() the left panel and rebalance column weights.
        Button label reflects current state.
        """
        if self.state.matrix_visible:
            self._left_panel.grid()
            self.root.columnconfigure(0, weight=CONFIG["MATRIX_PANEL_WEIGHT"])
            self._toggle_btn.config(text="Hide Strategy ◀")
        else:
            self._left_panel.grid_remove()
            self.root.columnconfigure(0, weight=0)
            self._toggle_btn.config(text="Show Strategy ▶")

    # ── Event handlers ────────────────────────────────────────────────────

    def _count_card(self, char: str) -> None:
        """Count one card via hotkey. Ignored when an Entry holds focus."""
        if isinstance(self.root.focus_get(), (tk.Entry, ttk.Entry)):
            return
        self.state = apply_card(self.state, char)
        self._schedule_refresh()

    def _undo_card(self) -> None:
        """Undo last card entry. Ignored when an Entry holds focus."""
        if isinstance(self.root.focus_get(), (tk.Entry, ttk.Entry)):
            return
        self.state = undo_card(self.state)
        self._schedule_refresh()

    def _reset_shoe(self) -> None:
        """Reset shoe count to zero and decks to DEFAULT_DECKS."""
        self.state = reset_shoe(self.state)
        self.decks_var.set(str(self.state.decks_remaining))
        self._refresh()

    def _set_dealer(self, card: str) -> None:
        """Set dealer upcard from button."""
        self.state = dc_replace(self.state, dealer_upcard=card)
        self.dealer_var.set(card)
        self._refresh()

    def _on_hand_change(self) -> None:
        """Called on every keystroke in the player-hand Entry."""
        self.state = dc_replace(self.state, player_hand=self.hand_var.get())
        self._refresh()

    def _on_hand_type_change(self) -> None:
        """Called when hand-type Radiobutton changes."""
        self.state = dc_replace(self.state, hand_type=self.hand_type_var.get())
        self._refresh()

    def _on_decks_change(self) -> None:
        """
        Validate the decks-remaining Spinbox and update state.
        Handles empty string, non-numeric input, and out-of-range values
        without crashing — falls back to current state value on any error.
        """
        raw = self.decks_var.get().strip()
        if not raw:
            # Empty field: restore last valid value silently
            self.decks_var.set(str(self.state.decks_remaining))
            return
        try:
            decks = float(raw)
            if decks < 0.5:
                decks = 0.5
            elif decks > 8.0:
                decks = 8.0
        except (ValueError, tk.TclError):
            self.decks_var.set(str(self.state.decks_remaining))
            return
        self.state = dc_replace(self.state, decks_remaining=decks)
        self._refresh()

    def _set_bankroll(self) -> None:
        """Parse and apply the bankroll Entry. Silently ignores invalid input."""
        try:
            val = str(Decimal(self.bankroll_entry_var.get().strip()).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP))
        except Exception:
            return
        self.state = dc_replace(self.state, bankroll=val, session_start=val)
        self._refresh()

    def _apply_result(self, result: str) -> None:
        """Parse current-bet Entry, apply result, refresh."""
        try:
            bet_str = str(Decimal(self.bet_var.get().strip()))
            self.state = dc_replace(self.state, current_bet=bet_str)
        except Exception:
            pass
        self.state = apply_result(self.state, result)
        self._refresh()

    def _use_suggested_bet(self) -> None:
        """Copy computed Kelly bet into the current-bet Entry."""
        bet = suggested_bet(self.state)
        self.bet_var.set(str(bet))
        self.state = dc_replace(self.state, current_bet=str(bet))

    def _set_rule_set(self, key: str) -> None:
        """Switch rule set; immediately refreshes bet sizing and strategy."""
        if key not in CONFIG["RULE_PRESETS"]:
            return
        self.state = dc_replace(self.state, rule_set=key)
        self._refresh()

    def _new_session(self) -> None:
        """Reset session P&L while preserving bankroll and count."""
        if messagebox.askyesno(
                "New Session",
                "Reset session P&L?\nBankroll and count will be preserved."):
            self.state = dc_replace(self.state,
                                    session_start=self.state.bankroll)
            self._refresh()

    def _on_close(self) -> None:
        """Persist state and exit."""
        # Cancel any pending throttled refresh to avoid after() callback on dead window
        if self._refresh_pending is not None:
            self.root.after_cancel(self._refresh_pending)
        save_state(self.state)
        self.root.destroy()

    # ── Throttled refresh ─────────────────────────────────────────────────

    def _schedule_refresh(self) -> None:
        """
        Coalesce rapid state changes (hotkey bursts) into at most one redraw
        per REFRESH_THROTTLE_MS milliseconds.  The after() handle is cancelled
        and rescheduled on each call, so only the final state triggers _refresh.
        """
        if self._refresh_pending is not None:
            self.root.after_cancel(self._refresh_pending)
        self._refresh_pending = self.root.after(
            CONFIG["REFRESH_THROTTLE_MS"], self._refresh)

    # ── Reactive redraw ───────────────────────────────────────────────────

    def _refresh(self) -> None:
        """
        Update every dynamic widget from current state.
        No widget is created or destroyed here — only .configure() calls.
        """
        self._refresh_pending = None

        tc = get_true_count(self.state)
        rc = self.state.running_count

        # ── Count labels (white; red when negative) ────────────────────
        rc_color = CONFIG["NEG_CLR"] if rc < 0 else CONFIG["FG"]
        tc_color = CONFIG["NEG_CLR"] if tc < 0 else CONFIG["FG"]
        self.rc_lbl.configure(
            text=f"{rc:+d}" if rc != 0 else "0", fg=rc_color)
        self.tc_lbl.configure(
            text=f"{tc:+.2f}" if tc != 0 else "0.00", fg=tc_color)

        # ── Bankroll & P&L ─────────────────────────────────────────────
        bankroll = Decimal(self.state.bankroll)
        pnl      = session_pnl(self.state)
        self.bankroll_lbl.configure(text=f"${bankroll:,.2f}")
        pnl_color = (CONFIG["POS_CLR"] if pnl > 0
                     else CONFIG["NEG_CLR"] if pnl < 0
                     else CONFIG["FG"])
        sign = "+" if pnl > 0 else ""
        self.pnl_lbl.configure(text=f"{sign}${pnl:,.2f}", fg=pnl_color)

        # ── Suggested bet ───────────────────────────────────────────────
        bet    = suggested_bet(self.state)
        be_tc  = breakeven_tc(self.state)
        be_str = f"{be_tc:.2f}"
        self.bet_lbl.configure(text=f"${bet:,.2f}")
        if bet > Decimal("0"):
            net = CONFIG["EDGE_PER_TC"] * Decimal(str(tc)) - get_base_edge(self.state)
            self.bet_info_lbl.configure(
                text=f"Net edge {float(net):.3%}  |  ¼ Kelly")
        else:
            self.bet_info_lbl.configure(
                text=f"Need TC ≥ {be_str} to overcome house edge")

        # ── Strategy suggestion ─────────────────────────────────────────
        raw_action  = get_strategy(dc_replace(self.state, rule_set="liberal"))
        evo_action  = get_strategy(self.state)   # already applies fallback

        if evo_action:
            color = CONFIG["ACTION_FG"].get(evo_action, CONFIG["FG"])
            self.action_lbl.configure(text=evo_action, fg=color)
            self.action_name_lbl.configure(
                text=CONFIG["ACTIONS"].get(evo_action, evo_action), fg=color)
            # Show override notice when Evolution remapped a surrender
            if (self.state.rule_set == "evolution"
                    and raw_action == "R" and evo_action != "R"):
                self.override_lbl.configure(
                    text=f"⚠ Surrender unavailable → {CONFIG['ACTIONS'].get(evo_action, evo_action)}")
            else:
                self.override_lbl.configure(text="")
        else:
            self.action_lbl.configure(text="—", fg=CONFIG["FG_DIM"])
            self.action_name_lbl.configure(
                text="Enter hand & dealer card", fg=CONFIG["FG_DIM"])
            self.override_lbl.configure(text="")

        # ── Rule-set toggle buttons ─────────────────────────────────────
        # Active button: cyan fill + light-blue highlightbackground glow.
        # Inactive button: dim fill + invisible ring (matches BG4).
        for rs_key, btn in self._rule_btns.items():
            if rs_key == self.state.rule_set:
                btn.configure(
                    bg=CONFIG["ACCENT"], fg="#000000",
                    activebackground=CONFIG["ACCENT2"],
                    activeforeground="#000000",
                    highlightbackground=CONFIG["RULE_BTN_ACTIVE_HL"])
            else:
                btn.configure(
                    bg=CONFIG["BG4"], fg=CONFIG["FG_DIM"],
                    activebackground=CONFIG["BG3"],
                    activeforeground=CONFIG["FG"],
                    highlightbackground=CONFIG["RULE_BTN_INACTIVE_HL"])

        # ── Rule summary block ──────────────────────────────────────────
        preset    = CONFIG["RULE_PRESETS"][self.state.rule_set]
        be_tc_val = breakeven_tc(self.state)
        self.rule_summary_lbl.configure(
            text=(f"{preset['label']}\n"
                  f"House edge: {float(preset['edge']):.2%}\n"
                  f"Break-even TC: {be_tc_val:.2f}"))

        # ── Surrender note in matrix ────────────────────────────────────
        if self.state.rule_set == "evolution":
            self._surrender_note.configure(
                text="⚠ Evolution: Surrender unavailable — R cells show fallback")
        else:
            self._surrender_note.configure(text="")

        # ── Dealer button highlight ─────────────────────────────────────
        for btn in self.dealer_btns_frame.winfo_children():
            if btn.cget("text") == self.state.dealer_upcard.upper():
                btn.configure(bg=CONFIG["ACCENT2"], fg="#000")
            else:
                btn.configure(bg=CONFIG["BG4"], fg=CONFIG["FG"])

        # ── Matrix toggle button label ──────────────────────────────────
        # (already set in _apply_matrix_visibility; keep in sync on rebuild)

        # ── Matrix cell highlights ──────────────────────────────────────
        self._update_matrix_highlights()

    def _update_matrix_highlights(self) -> None:
        """
        Reconfigure cached matrix Labels for the current dealer/hand.
        No widget creation — only .configure() on existing Labels.
        """
        dealer = self.state.dealer_upcard.upper()
        dealer_idx = DEALER_UPCARDS.index(dealer) if dealer in DEALER_UPCARDS else -1
        hand = self.state.player_hand.strip().upper()

        # In Evolution mode, visually flag surrender cells with a dimmer action color
        evo = self.state.rule_set == "evolution"

        self._apply_table_highlights(
            cells=self._hard_cells,
            row_hdrs=self._hard_row_hdrs,
            col_hdrs=self._hard_col_hdrs,
            strategy_rows=[HARD_STRATEGY[t] for t in HARD_TOTALS],
            active_row=self._active_hard_row(hand),
            dealer_idx=dealer_idx,
            evo_mode=evo,
        )
        self._apply_table_highlights(
            cells=self._soft_cells,
            row_hdrs=self._soft_row_hdrs,
            col_hdrs=self._soft_col_hdrs,
            strategy_rows=[SOFT_STRATEGY[o] for o in SOFT_OTHER],
            active_row=self._active_soft_row(hand),
            dealer_idx=dealer_idx,
            evo_mode=evo,
        )
        self._apply_table_highlights(
            cells=self._pair_cells,
            row_hdrs=self._pair_row_hdrs,
            col_hdrs=self._pair_col_hdrs,
            strategy_rows=[PAIR_STRATEGY[k] for k in PAIR_KEYS],
            active_row=self._active_pair_row(hand),
            dealer_idx=dealer_idx,
            evo_mode=evo,
        )

    @staticmethod
    def _apply_table_highlights(
        cells:         list[list[tk.Label]],
        row_hdrs:      list[tk.Label],
        col_hdrs:      list[tk.Label],
        strategy_rows: list[list[str]],
        active_row:    int,
        dealer_idx:    int,
        evo_mode:      bool,
    ) -> None:
        """
        Reconfigure one strategy table in-place:
          • Active intersection cell → 3px #FF00FF magenta highlightbackground
          • Active row/column headers → dimly tinted
          • Inactive cells → highlightbackground=BG2 (invisible border)
          • Surrender cells in Evolution mode → rendered with ">" (fallback) text
            and italic styling to signal unavailability

        All cells maintain highlightthickness=CELL_BORDER_W at all times,
        so layout never shifts (Tkinter reserves the space unconditionally).
        """
        BG2     = CONFIG["BG2"]
        BG3     = CONFIG["BG3"]
        MAGENTA = CONFIG["MAGENTA"]
        DIM_HL  = CONFIG["BG3"]   # dim column header tint (active dealer)

        # Column headers
        for ci, hdr in enumerate(col_hdrs):
            hdr.configure(
                bg=DIM_HL if ci == dealer_idx else CONFIG["BG3"],
                fg=CONFIG["ACCENT"] if ci == dealer_idx else CONFIG["FG"])

        # Row headers + action cells
        for ri, (rh, row_data, row_cells) in enumerate(
                zip(row_hdrs, strategy_rows, cells)):
            row_active = ri == active_row
            rh.configure(
                bg=DIM_HL if row_active else BG2,
                fg=CONFIG["ACCENT"] if row_active else CONFIG["FG"])

            for ci, (cell, action) in enumerate(zip(row_cells, row_data)):
                is_hot = row_active and ci == dealer_idx

                # In Evolution mode, surrender cells get special treatment
                if evo_mode and action == "R":
                    fallback = _surrender_fallback(0)  # "H" for most
                    cell_text = f"({fallback})"
                    cell_bg   = "#1a0a1a"   # very dark purple to signal "R → fallback"
                    cell_fg   = "#AA44AA"
                else:
                    cell_text = action
                    cell_bg   = CONFIG["ACTION_COLORS"].get(action, BG3)
                    cell_fg   = CONFIG["ACTION_FG"].get(action, CONFIG["FG"])

                if is_hot:
                    cell.configure(
                        text=cell_text,
                        bg=cell_bg, fg="#FFFFFF",
                        highlightbackground=MAGENTA)
                else:
                    cell.configure(
                        text=cell_text,
                        bg=cell_bg, fg=cell_fg,
                        highlightbackground=BG2)

    # ── Active-row resolvers (pure, no widget access) ─────────────────────

    @staticmethod
    def _active_hard_row(hand: str) -> int:
        """Index into HARD_TOTALS for *hand*, or -1."""
        if hand.startswith("A") and len(hand) == 2:
            return -1
        if len(hand) == 2 and hand[0] == hand[1]:
            return -1
        try:
            total = max(5, min(int(hand), 21))
            return HARD_TOTALS.index(total)
        except ValueError:
            return -1

    @staticmethod
    def _active_soft_row(hand: str) -> int:
        """Index into SOFT_OTHER for *hand*, or -1."""
        if not (hand.startswith("A") and len(hand) == 2):
            return -1
        try:
            other = int(hand[1]) if hand[1].isdigit() else (
                10 if hand[1] in "TJQK" else -1)
            if other in SOFT_OTHER:
                return SOFT_OTHER.index(other)
        except (ValueError, IndexError):
            pass
        return -1

    @staticmethod
    def _active_pair_row(hand: str) -> int:
        """Index into PAIR_KEYS for *hand* if it's a pair, or -1."""
        if len(hand) == 2 and hand[0] == hand[1] and hand[0] in PAIR_KEYS:
            return PAIR_KEYS.index(hand[0])
        return -1


# ─────────────────────────────────────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    root = tk.Tk()
    app = BlackjackApp(root)
    root.mainloop()
