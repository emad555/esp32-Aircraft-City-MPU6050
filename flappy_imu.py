"""Flappy MPU: shake to flap, tilt to glide and steer past pipes."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import math
import random

import pygame

from game import TiltController, clamp, deadzone
from salvage import Audio


WIDTH, HEIGHT = 1100, 720
FPS = 60
GROUND = 632
BIRD_RADIUS = 19
PIPE_WIDTH = 92
SHAKE_THRESHOLD = 4.0
WHITE = (243, 249, 255)
MUTED = (153, 188, 204)
GOLD = (255, 211, 90)
CYAN = (82, 228, 245)
GREEN = (85, 207, 126)


@dataclass
class Pipe:
    x: float
    gap_y: float
    gap_size: float
    feather: bool = False
    scored: bool = False
    claimed: bool = False

    @property
    def top(self) -> float:
        return self.gap_y - self.gap_size / 2

    @property
    def bottom(self) -> float:
        return self.gap_y + self.gap_size / 2


def circle_hits_rect(x: float, y: float, radius: float, rect: pygame.Rect) -> bool:
    nearest_x = clamp(x, rect.left, rect.right)
    nearest_y = clamp(y, rect.top, rect.bottom)
    return (x - nearest_x) ** 2 + (y - nearest_y) ** 2 < radius ** 2


class FlappyGame:
    def __init__(self, seed: int | None = None) -> None:
        self.rng = random.Random(seed)
        self.best = 0
        self.reset()

    def reset(self) -> None:
        self.x = 260.0
        self.y = 350.0
        self.vy = 0.0
        self.score = 0
        self.passed = 0
        self.shield = 0
        self.invulnerable = 0.0
        self.flap_cooldown = 0.0
        self.flap_visual = 0.0
        self.elapsed = 0.0
        self.spawn_timer = 1.2
        self.spawn_count = 0
        self.last_gap = 350.0
        self.pipes: list[Pipe] = []
        self.trail: list[tuple[float, float]] = []
        self.status = "ready"
        self.paused = False
        self.events: list[str] = []

    def _spawn_pipe(self) -> None:
        gap = max(175.0, 220.0 - self.passed * 2.5)
        self.last_gap = clamp(self.last_gap + self.rng.uniform(-112, 112), 205, 420)
        self.spawn_count += 1
        self.pipes.append(Pipe(WIDTH + 60.0, self.last_gap, gap, feather=self.spawn_count % 4 == 0))

    def _flap(self) -> None:
        if self.flap_cooldown > 0:
            return
        self.vy = -355.0
        self.flap_cooldown = 0.21
        self.flap_visual = 0.24

    def _crash(self) -> None:
        if self.invulnerable > 0:
            return
        if self.shield:
            self.shield = 0
            self.invulnerable = 1.2
            self.vy = -180
            self.events.append("pickup")
        else:
            self.status = "over"
            self.best = max(self.best, self.score)
            self.events.append("hit")

    def update(self, dt: float, roll: float, pitch: float, flap: bool) -> None:
        dt = clamp(dt, 0.0, 0.05)
        self.events.clear()
        if self.paused:
            return
        self.elapsed += dt
        roll, pitch = clamp(roll, -1, 1), clamp(pitch, -1, 1)
        desired_x = 260 + 95 * roll
        self.x += (desired_x - self.x) * min(1.0, 6.0 * dt)

        if self.status == "ready":
            self.y = 350 + math.sin(self.elapsed * 3.1) * 8
            if not flap:
                return
            self.status = "playing"
            self._flap()
        elif self.status == "over":
            if flap:
                self.reset()
                self.status = "playing"
                self._flap()
            return

        self.flap_cooldown = max(0.0, self.flap_cooldown - dt)
        self.flap_visual = max(0.0, self.flap_visual - dt)
        self.invulnerable = max(0.0, self.invulnerable - dt)
        if flap:
            self._flap()

        gravity = 840 + 210 * pitch  # Positive pitch now means a steeper downward glide.
        self.vy = min(530.0, self.vy + gravity * dt)
        self.y += self.vy * dt
        self.trail.append((self.x, self.y))
        self.trail = self.trail[-24:]
        if self.y - BIRD_RADIUS < 96 or self.y + BIRD_RADIUS > GROUND:
            self._crash()
            if self.status == "over":
                return
            self.y = clamp(self.y, 96 + BIRD_RADIUS, GROUND - BIRD_RADIUS)

        speed = 245 + min(85, self.passed * 4)
        self.spawn_timer -= dt
        if self.spawn_timer <= 0:
            self._spawn_pipe()
            self.spawn_timer += 1.43
        for pipe in self.pipes:
            pipe.x -= speed * dt
            if not pipe.scored and pipe.x + PIPE_WIDTH < self.x:
                pipe.scored = True
                self.passed += 1
                self.score += 1
                self.best = max(self.best, self.score)
                self.events.append("pickup")
            if pipe.feather and not pipe.claimed:
                if math.hypot(self.x - (pipe.x + PIPE_WIDTH / 2), self.y - pipe.gap_y) < 30:
                    pipe.claimed = True
                    self.shield = 1
                    self.score += 2
                    self.best = max(self.best, self.score)
                    self.events.append("pickup")
            top = pygame.Rect(round(pipe.x), 0, PIPE_WIDTH, round(pipe.top))
            bottom = pygame.Rect(round(pipe.x), round(pipe.bottom), PIPE_WIDTH, GROUND - round(pipe.bottom))
            if circle_hits_rect(self.x, self.y, BIRD_RADIUS - 3, top) or circle_hits_rect(
                    self.x, self.y, BIRD_RADIUS - 3, bottom):
                self._crash()
                if self.status == "over":
                    break
        self.pipes = [pipe for pipe in self.pipes if pipe.x + PIPE_WIDTH > -20]


class Renderer:
    def __init__(self) -> None:
        self.title = pygame.font.SysFont("consolas", 27, bold=True)
        self.score = pygame.font.SysFont("consolas", 46, bold=True)
        self.small = pygame.font.SysFont("consolas", 16)
        self.big = pygame.font.SysFont("consolas", 52, bold=True)
        self.sky = pygame.Surface((WIDTH, HEIGHT))
        for y in range(HEIGHT):
            t = y / HEIGHT
            pygame.draw.line(self.sky, (int(12 + 66 * t), int(35 + 110 * t), int(76 + 118 * t)),
                             (0, y), (WIDTH, y))
        stars = random.Random(17)
        for _ in range(95):
            pygame.draw.circle(self.sky, (127, 202, 224),
                               (stars.randrange(WIDTH), stars.randrange(450)), stars.choice((1, 1, 2)))

    @staticmethod
    def _cloud(surface: pygame.Surface, x: float, y: float, scale: float) -> None:
        color = (143, 218, 222)
        for dx, dy, radius in ((0, 0, 23), (25, -9, 27), (55, 1, 22), (25, 9, 24)):
            pygame.draw.circle(surface, color, (round(x + dx * scale), round(y + dy * scale)), round(radius * scale))

    @staticmethod
    def _pipe(surface: pygame.Surface, pipe: Pipe) -> None:
        x = round(pipe.x)
        top = round(pipe.top)
        bottom = round(pipe.bottom)
        for y, height in ((0, top), (bottom, GROUND - bottom)):
            if height <= 0:
                continue
            pygame.draw.rect(surface, (21, 87, 83), (x + 5, y + 5, PIPE_WIDTH, height))
            pygame.draw.rect(surface, (43, 148, 113), (x, y, PIPE_WIDTH, height))
            pygame.draw.rect(surface, GREEN, (x + 8, y, 13, height))
            pygame.draw.rect(surface, (21, 80, 80), (x + PIPE_WIDTH - 13, y, 13, height))
        pygame.draw.rect(surface, (30, 97, 86), (x - 7, top - 26, PIPE_WIDTH + 14, 30))
        pygame.draw.rect(surface, GREEN, (x - 9, top - 30, PIPE_WIDTH + 14, 27), border_radius=4)
        pygame.draw.rect(surface, (30, 97, 86), (x - 7, bottom, PIPE_WIDTH + 14, 30))
        pygame.draw.rect(surface, GREEN, (x - 9, bottom, PIPE_WIDTH + 14, 27), border_radius=4)
        if pipe.feather and not pipe.claimed:
            cx, cy = x + PIPE_WIDTH // 2, round(pipe.gap_y)
            pygame.draw.circle(surface, (53, 155, 164), (cx, cy), 26, 2)
            pygame.draw.polygon(surface, GOLD, [(cx, cy - 20), (cx + 15, cy), (cx, cy + 20), (cx - 15, cy)])
            pygame.draw.polygon(surface, WHITE, [(cx, cy - 10), (cx + 7, cy), (cx, cy + 10), (cx - 7, cy)])

    @staticmethod
    def _bird(surface: pygame.Surface, game: FlappyGame) -> None:
        x, y = round(game.x), round(game.y)
        for index, (tx, ty) in enumerate(game.trail):
            pygame.draw.circle(surface, (39, 128 + index * 3, 170 + index * 2),
                               (round(tx), round(ty)), max(1, index // 5))
        if game.invulnerable == 0 or int(game.elapsed * 12) % 2 == 0:
            wing_lift = 15 if game.flap_visual else -3 + round(5 * math.sin(game.elapsed * 16))
            pygame.draw.ellipse(surface, (25, 70, 96), (x - 28, y - 13, 49, 38))
            pygame.draw.polygon(surface, (47, 158, 187),
                                [(x - 15, y + 2), (x - 37, y + 13 - wing_lift), (x - 19, y + 19)])
            pygame.draw.ellipse(surface, CYAN, (x - 23, y - 17, 47, 36))
            pygame.draw.ellipse(surface, (30, 113, 154), (x - 11, y - 1 - wing_lift, 27, 18))
            pygame.draw.polygon(surface, GOLD, [(x + 18, y - 2), (x + 36, y + 4), (x + 18, y + 10)])
            pygame.draw.circle(surface, WHITE, (x + 9, y - 8), 8)
            pygame.draw.circle(surface, (17, 35, 58), (x + 12, y - 8), 3)
        if game.shield:
            pygame.draw.circle(surface, GOLD, (x, y), 29, 3)

    def draw(self, surface: pygame.Surface, game: FlappyGame, controller: TiltController | None = None) -> None:
        surface.blit(self.sky, (0, 0))
        shift = game.elapsed * (35 if game.status == "playing" else 12)
        for index in range(7):
            self._cloud(surface, ((index * 245 - shift * 0.7) % (WIDTH + 260)) - 120,
                        105 + (index * 61) % 270, 0.8 + (index % 3) * 0.2)
        # Far city skyline, then the close pipe layer.
        for index in range(20):
            x = (index * 82 - shift * 0.45) % (WIDTH + 100) - 60
            height = 85 + (index * 37) % 115
            pygame.draw.rect(surface, (40, 99, 119), (x, GROUND - height, 70, height))
            for wy in range(GROUND - height + 16, GROUND - 14, 22):
                pygame.draw.rect(surface, (75, 145, 153), (x + 14, wy, 7, 9))
                pygame.draw.rect(surface, (75, 145, 153), (x + 46, wy, 7, 9))
        for pipe in game.pipes:
            self._pipe(surface, pipe)
        self._bird(surface, game)
        pygame.draw.rect(surface, (31, 108, 88), (0, GROUND, WIDTH, HEIGHT - GROUND))
        pygame.draw.rect(surface, (117, 212, 135), (0, GROUND, WIDTH, 7))
        for x in range(0, WIDTH, 47):
            pygame.draw.rect(surface, (58, 141, 101), (x, GROUND + 29, 24, 5))

        hud = pygame.Surface((WIDTH, 76), pygame.SRCALPHA)
        hud.fill((8, 31, 55, 206))
        surface.blit(hud, (0, 0))
        surface.blit(self.title.render("FLAPPY MPU", True, WHITE), (25, 21))
        score = self.score.render(f"{game.score:02d}", True, GOLD)
        surface.blit(score, score.get_rect(center=(WIDTH // 2, 39)))
        surface.blit(self.small.render(f"BEST {game.best:02d}", True, WHITE), (700, 27))
        surface.blit(self.small.render("FEATHER SHIELD" if game.shield else "NO SHIELD", True,
                                       GOLD if game.shield else MUTED), (857, 27))
        if controller and controller.motion_live:
            sensor = f"MPU6050 LIVE  |  SHAKE {controller.shock:.1f}"
        elif controller and controller.live:
            sensor = "TILT LIVE  |  upload updated sketch for shake"
        else:
            sensor = "KEYBOARD: SPACE FLAP, WASD TILT"
        surface.blit(self.small.render(sensor, True, WHITE), (26, HEIGHT - 56))
        surface.blit(self.small.render("C CENTER   P PAUSE   R RESTART   M MUTE   ESC QUIT", True, WHITE),
                     (585, HEIGHT - 56))

        if game.status == "ready" or game.status == "over" or game.paused:
            shade = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
            shade.fill((4, 22, 42, 146))
            surface.blit(shade, (0, 0))
            heading = "READY TO FLY" if game.status == "ready" else ("PAUSED" if game.paused else "FLIGHT OVER")
            title = self.big.render(heading, True, WHITE if game.status != "over" else GOLD)
            surface.blit(title, title.get_rect(center=(WIDTH // 2, HEIGHT // 2 - 52)))
            if game.status == "over":
                detail = f"SCORE {game.score}   BEST {game.best}"
            else:
                detail = "Shake the MPU6050 or press Space to flap"
            line = self.title.render(detail, True, CYAN)
            surface.blit(line, line.get_rect(center=(WIDTH // 2, HEIGHT // 2 + 5)))
            help_text = "Tilt forward/back to glide; left/right to steer" if not game.paused else "Press P to resume"
            help_line = self.small.render(help_text, True, WHITE)
            surface.blit(help_line, help_line.get_rect(center=(WIDTH // 2, HEIGHT // 2 + 55)))


def main() -> None:
    parser = argparse.ArgumentParser(description="Flappy MPU: shake-to-flap game for ESP32 MPU6050")
    parser.add_argument("--port", help="Serial port, for example COM5 or /dev/ttyUSB0 (default: auto-detect)")
    parser.add_argument("--invert-roll", action="store_true", help="Reverse left/right steering")
    parser.add_argument("--invert-pitch", action="store_true", help="Reverse up/down glide control")
    parser.add_argument("--shake-threshold", type=float, default=SHAKE_THRESHOLD,
                        help="Shake strength needed to flap in m/s^2 (default: 4.0)")
    args = parser.parse_args()
    if not math.isfinite(args.shake_threshold) or args.shake_threshold <= 0:
        parser.error("--shake-threshold must be a positive number")

    pygame.mixer.pre_init(frequency=22050, size=-16, channels=1, buffer=512)
    pygame.init()
    screen = pygame.display.set_mode((WIDTH, HEIGHT))
    pygame.display.set_caption("Flappy MPU - ESP32 MPU6050")
    clock = pygame.time.Clock()
    game = FlappyGame()
    renderer = Renderer()
    audio = Audio()
    controller = TiltController(args.port)
    running = True
    try:
        while running:
            dt = clock.tick(FPS) / 1000.0
            flap = False
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        running = False
                    elif event.key == pygame.K_SPACE:
                        flap = True
                    elif event.key == pygame.K_c:
                        controller.center()
                    elif event.key == pygame.K_m:
                        audio.muted = not audio.muted
                    elif event.key == pygame.K_p and game.status != "over":
                        game.paused = not game.paused
                    elif event.key == pygame.K_r:
                        game.reset()
            controller.update()
            keys = pygame.key.get_pressed()
            roll = deadzone(controller.roll) * (-1 if args.invert_roll else 1) if controller.live else 0.0
            pitch = deadzone(controller.pitch) * (-1 if args.invert_pitch else 1) if controller.live else 0.0
            if keys[pygame.K_a] or keys[pygame.K_LEFT]:
                roll = -1.0
            if keys[pygame.K_d] or keys[pygame.K_RIGHT]:
                roll = 1.0
            if keys[pygame.K_w] or keys[pygame.K_UP]:
                pitch = -1.0
            if keys[pygame.K_s] or keys[pygame.K_DOWN]:
                pitch = 1.0
            if controller.motion_live and controller.shock >= args.shake_threshold:
                flap = True
            game.update(dt, roll, pitch, flap)
            for name in game.events:
                audio.play(name)
            renderer.draw(screen, game, controller)
            pygame.display.flip()
    finally:
        controller.close()
        pygame.quit()


if __name__ == "__main__":
    main()
