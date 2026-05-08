"""
Chess engine wrapper around python-chess + Stockfish.

Owns the authoritative game state. Vision detects moves but the engine
is the only place that validates and applies them.
"""
import chess
import chess.engine

from config.settings import STOCKFISH_PATH


class ChessEngine:
    def __init__(self, stockfish_path=None):
        path = stockfish_path or STOCKFISH_PATH
        self.board = chess.Board()
        self.engine = chess.engine.SimpleEngine.popen_uci(path)
        self.move_history = []

    def set_difficulty(self, level):
        skill_levels = {"Easy": 2, "Medium": 10, "Hard": 20}
        skill = skill_levels.get(level, 10)
        self.engine.configure({"Skill Level": skill})

    def make_player_move(self, move_text):
        """Apply a move from UCI text (e.g. 'e2e4'). Auto-promotes to queen."""
        try:
            move = chess.Move.from_uci(move_text)
        except ValueError:
            return False, "Invalid move format"

        if move not in self.board.legal_moves:
            # Try with queen promotion appended
            try:
                promotion_move = chess.Move.from_uci(move_text + "q")
                if promotion_move in self.board.legal_moves:
                    move = promotion_move
                else:
                    return False, "Illegal chess move"
            except ValueError:
                return False, "Illegal chess move"

        captured_piece = self.board.piece_at(move.to_square)
        self.board.push(move)
        self.move_history.append(str(move))
        return True, {
            "uci": str(move),
            "from": chess.square_name(move.from_square),
            "to": chess.square_name(move.to_square),
            "captured_piece": captured_piece,
        }

    def get_best_move(self, time_limit=0.5):
        result = self.engine.play(
            self.board,
            chess.engine.Limit(time=time_limit),
        )
        return result.move

    def make_ai_move(self, time_limit=0.5):
        move = self.get_best_move(time_limit)
        captured_piece = self.board.piece_at(move.to_square)
        self.board.push(move)
        self.move_history.append(str(move))
        return {
            "uci": str(move),
            "from": chess.square_name(move.from_square),
            "to": chess.square_name(move.to_square),
            "captured_piece": captured_piece,
            "fen": self.board.fen(),
        }

    def reset(self):
        self.board.reset()
        self.move_history.clear()

    def close(self):
        try:
            self.engine.quit()
        except Exception:
            pass
