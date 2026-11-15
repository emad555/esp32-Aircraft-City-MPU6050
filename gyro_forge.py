"""Gyro Forge: tilt gravity, twist a shield, and shake to repel hazards."""

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
FIELD = pygame.Rect(65, 111, 970, 532)
BALL_RADIUS = 17
TARGET = 12
ROUND_SECONDS = 90.0
SHAKE_THRESHOLD = 4.0  # Acceleration beyond gravity, in m/s^2.
PULSE_COOLDOWN = 5.5
WHITE = (231, 246, 255)
MUTED = (139, 169, 189)
CYAN = (72, 233, 244)
GOLD = (255, 211, 99)
RED = (255, 91, 120)


@dataclass
class Crystal:
    pos: pygame.Vector2
    phase: float


@dataclass
class Hazard:
    pos: pygame.Vector2
    vel: pygame.Vector2
    radius: int
    stunned: float = 0.0


class Forge:
    """A motion-first arena: the input changes acceleration, not position."""

    def __init__(self, seed: int | None = None) -> None:
        self.rng = random.Random(seed)
        self.reset()

    def reset(self) -> None:
        self.ball = pygame.Vector2(FIELD.center)
        self.velocity = pygame.Vector2()
        self.shield_angle = -90.0
        self.health = 3
        self.score = 0
        self.collected = 0
        self.combo = 0
        self.combo_time = 0.0
        self.remaining = ROUND_SECONDS
        self.elapsed = 0.0
        self.pulse_cooldown = 0.0
        self.pulse_visual = 0.0
        self.shield_flash = 0.0
        self.invulnerable = 0.0
        self.status = "playing"
        self.paused = False
        self.events: list[str] = []
        self.trail: list[pygame.Vector2] = []
        self.crystals: list[Crystal] = []
        self.hazards: list[Hazard] = []
        for _ in range(3):
            self._spawn_hazard()
        for _ in range(3):
            self._spawn_crystal()

    def _point(self, margin: int = 43) -> pygame.Vector2:
        return pygame.Vector2(self.rng.uniform(FIELD.left + margin, FIELD.right - margin),
                              self.rng.uniform(FIELD.top + margin, FIELD.bottom - margin))

    def _spawn_crystal(self) -> None:
        for _ in range(100):
            point = self._point()
            if point.distance_to(self.ball) < 130:
                continue
            if any(point.distance_to(c.pos) < 90 for c in self.crystals):
                continue
            if any(point.distance_to(h.pos) < h.radius + 50 for h in self.hazards):
                continue
            self.crystals.append(Crystal(point, self.rng.random() * math.tau))
            return
        self.crystals.append(Crystal(self._point(), self.rng.random() * math.tau))

    def _spawn_hazard(self) -> None:
        for _ in range(100):
            point = self._point(65)
            if point.distance_to(self.ball) > 180 and all(point.distance_to(h.pos) > 85 for h in self.hazards):
                break
        direction = pygame.Vector2(1, 0).rotate(self.rng.uniform(0, 360))
        self.hazards.append(Hazard(point, direction * self.rng.uniform(65, 100), self.rng.randint(15, 22)))

    @staticmethod
    def _angle_difference(first: float, second: float) -> float:
        return (first - second + 180.0) % 360.0 - 180.0

    def _pulse(self) -> None:
        if self.pulse_cooldown > 0:
            return
        self.pulse_cooldown = PULSE_COOLDOWN
        self.pulse_visual = 0.5
        self.invulnerable = max(self.invulnerable, 0.25)
        self.events.append("pickup")
        for hazard in self.hazards:
            away = hazard.pos - self.ball
            distance = away.length()
            if distance < 230:
                if distance < 1:
                    away = pygame.Vector2(1, 0)
                hazard.vel = away.normalize() * 430
                hazard.stunned = 0.8

    def update(self, dt: float, tilt_x: float, tilt_y: float, yaw_rate: float, pulse: bool) -> None:
        dt = clamp(dt, 0.0, 0.05)
        self.events.clear()
        if self.paused or self.status != "playing":
            return
        self.elapsed += dt
        self.remaining = max(0.0, self.remaining - dt)
        self.invulnerable = max(0.0, self.invulnerable - dt)
        self.pulse_cooldown = max(0.0, self.pulse_cooldown - dt)
        self.pulse_visual = max(0.0, self.pulse_visual - dt)
        self.shield_flash = max(0.0, self.shield_flash - dt)
        self.combo_time = max(0.0, self.combo_time - dt)
        if self.combo_time == 0:
            self.combo = 0
        if self.remaining == 0:
            self.status = "lost"
            self.events.append("lose")
            return

        if abs(yaw_rate) < 4:
            yaw_rate = 0.0  # Ignore gyro drift while the controller is still.
        self.shield_angle = (self.shield_angle + clamp(yaw_rate, -300, 300) * dt) % 360
        if pulse:
            self._pulse()

        gravity = pygame.Vector2(clamp(tilt_x, -1, 1), clamp(tilt_y, -1, 1)) * 920
        self.velocity += gravity * dt
        self.velocity *= max(0.0, 1.0 - 1.25 * dt)
        if self.velocity.length() > 455:
            self.velocity.scale_to_length(455)
        self.ball += self.velocity * dt
        for axis, low, high in (("x", FIELD.left + BALL_RADIUS, FIELD.right - BALL_RADIUS),
                                ("y", FIELD.top + BALL_RADIUS, FIELD.bottom - BALL_RADIUS)):
            value = getattr(self.ball, axis)
            if value < low or value > high:
                setattr(self.ball, axis, clamp(value, low, high))
                setattr(self.velocity, axis, -getattr(self.velocity, axis) * 0.55)
        self.trail.append(self.ball.copy())
        self.trail = self.trail[-28:]

        for hazard in self.hazards:
            hazard.stunned = max(0.0, hazard.stunned - dt)
            if hazard.stunned == 0:
                pull = self.ball - hazard.pos
                if pull.length_squared() > 1:
                    hazard.vel += pull.normalize() * 28 * dt
                if hazard.vel.length() > 175:
                    hazard.vel.scale_to_length(175)
            hazard.pos += hazard.vel * dt
            for axis, low, high in (("x", FIELD.left + hazard.radius, FIELD.right - hazard.radius),
                                    ("y", FIELD.top + hazard.radius, FIELD.bottom - hazard.radius)):
                value = getattr(hazard.pos, axis)
                if value < low or value > high:
                    setattr(hazard.pos, axis, clamp(value, low, high))
                    setattr(hazard.vel, axis, -getattr(hazard.vel, axis))

        for crystal in self.crystals[:]:
            crystal.phase += dt * 2.8
            if crystal.pos.distance_to(self.ball) < BALL_RADIUS + 18:
                self.crystals.remove(crystal)
                self.collected += 1
                self.combo = min(5, self.combo + 1) if self.combo_time > 0 else 1
                self.combo_time = 4.0
                self.score += 100 * self.combo
                self.events.append("pickup")
                if self.collected >= TARGET:
                    self.status = "won"
                    self.events.append("win")
                    return
                if self.collected % 4 == 0:
                    self._spawn_hazard()
                self._spawn_crystal()

        if self.invulnerable == 0:
            for hazard in self.hazards:
                if hazard.stunned > 0:
                    continue
                away = hazard.pos - self.ball
                if away.length() >= BALL_RADIUS + hazard.radius:
                    continue
                angle = math.degrees(math.atan2(away.y, away.x))
                direction = away.normalize() if away.length_squared() else pygame.Vector2(1, 0)
                hazard.vel = direction * 340
                hazard.stunned = 0.5
                if abs(self._angle_difference(angle, self.shield_angle)) <= 63:
                    self.shield_flash = 0.35
                    self.score += 25
                    self.events.append("pickup")
                else:
                    self.health -= 1
                    self.combo = 0
                    self.combo_time = 0.0
                    self.invulnerable = 1.5
                    self.velocity -= direction * 135
                    self.events.append("hit")
                    if self.health <= 0:
                        self.status = "lost"
                        self.events.append("lose")
                break


class Renderer:
    def __init__(self) -> None:
        self.title = pygame.font.SysFont("consolas", 33, bold=True)
        self.hud = pygame.font.SysFont("consolas", 21, bold=True)
        self.small = pygame.font.SysFont("consolas", 16)
        self.big = pygame.font.SysFont("consolas", 48, bold=True)
        self.background = pygame.Surface((WIDTH, HEIGHT))
        for y in range(HEIGHT):
            mix = y / HEIGHT
            pygame.draw.line(self.background, (int(8 + 10 * mix), int(15 + 14 * mix), int(36 + 19 * mix)),
                             (0, y), (WIDTH, y))
        rng = random.Random(6050)
        for _ in range(180):
            pygame.draw.circle(self.background, (36, 67, 92),
                               (rng.randrange(WIDTH), rng.randrange(HEIGHT)), rng.choice((1, 1, 2)))
        for x in range(FIELD.left, FIELD.right + 1, 38):
            pygame.draw.line(self.background, (20, 47, 68), (x, FIELD.top), (x, FIELD.bottom))
        for y in range(FIELD.top, FIELD.bottom + 1, 38):
            pygame.draw.line(self.background, (20, 47, 68), (FIELD.left, y), (FIELD.right, y))
        pygame.draw.rect(self.background, (50, 127, 142), FIELD, 3, border_radius=14)

    def draw(self, surface: pygame.Surface, game: Forge, controller: TiltController | None = None) -> None:
        surface.blit(self.background, (0, 0))
        for index, pos in enumerate(game.trail):
            strength = (index + 1) / len(game.trail)
            pygame.draw.circle(surface, (int(22 + 29 * strength), int(66 + 86 * strength),
                                         int(81 + 90 * strength)), (round(pos.x), round(pos.y)),
                               max(1, round(5 * strength)))
        for crystal in game.crystals:
            x, y = round(crystal.pos.x), round(crystal.pos.y)
            pulse = round(3 * math.sin(crystal.phase))
            pygame.draw.circle(surface, (42, 103, 118), (x, y), 31 + pulse, 2)
            pygame.draw.polygon(surface, GOLD, [(x, y - 18), (x + 15, y), (x, y + 18), (x - 15, y)])
            pygame.draw.polygon(surface, (255, 248, 191), [(x, y - 11), (x + 9, y), (x, y + 11), (x - 9, y)])
        for hazard in game.hazards:
            x, y = round(hazard.pos.x), round(hazard.pos.y)
            color = (232, 122, 124) if hazard.stunned else RED
            pygame.draw.circle(surface, (95, 37, 66), (x, y), hazard.radius + 9, 2)
            for arm in range(6):
                angle = arm * math.tau / 6 + game.elapsed * 0.65
                dx, dy = math.cos(angle), math.sin(angle)
                pygame.draw.line(surface, color, (x + dx * (hazard.radius - 2), y + dy * (hazard.radius - 2)),
                                 (x + dx * (hazard.radius + 9), y + dy * (hazard.radius + 9)), 3)
            pygame.draw.circle(surface, (49, 27, 51), (x, y), hazard.radius)
            pygame.draw.circle(surface, color, (x, y), hazard.radius, 3)
            pygame.draw.circle(surface, (255, 172, 169), (x, y), 5)

        x, y = round(game.ball.x), round(game.ball.y)
        if game.pulse_visual > 0:
            progress = 1 - game.pulse_visual / 0.5
            pygame.draw.circle(surface, CYAN, (x, y), round(25 + 210 * progress), max(1, round(5 - 3 * progress)))
        if game.invulnerable == 0 or int(game.elapsed * 11) % 2 == 0:
            pygame.draw.circle(surface, (31, 106, 125), (x, y), 27, 2)
            pygame.draw.circle(surface, CYAN, (x, y), BALL_RADIUS)
            pygame.draw.circle(surface, (194, 255, 255), (x - 5, y - 5), 7)
        shield_color = WHITE if game.shield_flash else CYAN
        points = []
        for offset in range(-63, 64, 4):
            angle = math.radians(game.shield_angle + offset)
            points.append((x + math.cos(angle) * 37, y + math.sin(angle) * 37))
        if len(points) > 1:
            pygame.draw.lines(surface, shield_color, False, points, 7)

        surface.blit(self.title.render("GYRO FORGE", True, WHITE), (32, 18))
        surface.blit(self.hud.render(f"SCORE {game.score:05d}", True, GOLD), (345, 26))
        surface.blit(self.hud.render(f"CRYSTALS {game.collected:02d}/{TARGET}", True, CYAN), (580, 26))
        surface.blit(self.hud.render(f"TIME {math.ceil(game.remaining):02d}", True,
                                     RED if game.remaining < 15 else WHITE), (893, 26))
        pygame.draw.rect(surface, (28, 53, 72), (65, 81, 970, 7), border_radius=3)
        pygame.draw.rect(surface, CYAN, (65, 81, round(970 * game.collected / TARGET), 7), border_radius=3)
        surface.blit(self.small.render("SHIELD", True, MUTED), (66, 650))
        for index in range(3):
            pygame.draw.circle(surface, CYAN if index < game.health else (50, 79, 96),
                               (172 + index * 25, 661), 8, 2)
        ready = "READY" if game.pulse_cooldown == 0 else f"{game.pulse_cooldown:.1f}s"
        surface.blit(self.small.render(f"SHAKE PULSE {ready}", True, GOLD if game.pulse_cooldown == 0 else MUTED),
                     (285, 650))
        if controller and controller.motion_live:
            telemetry = (f"ROLL {controller.roll:+.0f}  PITCH {controller.pitch:+.0f}  "
                         f"TWIST {controller.yaw_rate:+.0f}/s  SHAKE {controller.shock:.1f}")
            status = "MPU6050 motion live"
        elif controller and controller.live:
            telemetry = "Tilt live | re-upload sketch for twist and shake"
            status = controller.message
        else:
            telemetry = "Keyboard demo: WASD tilt  Q/E twist  Space pulse"
            status = "Connect MPU6050 for full motion control"
        surface.blit(self.small.render(telemetry, True, CYAN if controller and controller.motion_live else MUTED),
                     (66, 679))
        surface.blit(self.small.render(status[:43], True, MUTED), (710, 651))
        surface.blit(self.small.render("C CENTER   P PAUSE   R RESTART   M MUTE", True, MUTED), (710, 679))
        if game.combo > 1 and game.status == "playing":
            surface.blit(self.hud.render(f"{game.combo}x COMBO", True, GOLD), (94, 127))

        if game.paused or game.status != "playing":
            shade = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
            shade.fill((3, 10, 25, 186))
            surface.blit(shade, (0, 0))
            heading = "PAUSED" if game.paused else ("FORGE COMPLETE" if game.status == "won" else "FORGE LOST")
            color = CYAN if game.paused or game.status == "won" else RED
            title = self.big.render(heading, True, color)
            surface.blit(title, title.get_rect(center=(WIDTH // 2, HEIGHT // 2 - 54)))
            detail = self.hud.render(f"{game.collected}/{TARGET} crystals   {game.score} points", True, WHITE)
            surface.blit(detail, detail.get_rect(center=(WIDTH // 2, HEIGHT // 2 + 9)))
            prompt = "P to resume" if game.paused else "R or Enter to try again"
            label = self.small.render(prompt, True, MUTED)
            surface.blit(label, label.get_rect(center=(WIDTH // 2, HEIGHT // 2 + 62)))


def main() -> None:
    parser = argparse.ArgumentParser(description="Gyro Forge: MPU6050 tilt, twist, and shake game")
    parser.add_argument("--port", help="Serial port, for example COM5 or /dev/ttyUSB0 (default: auto-detect)")
    parser.add_argument("--invert-roll", action="store_true", help="Reverse left/right tilt")
    parser.add_argument("--invert-pitch", action="store_true", help="Reverse up/down tilt")
    parser.add_argument("--invert-yaw", action="store_true", help="Reverse twist direction")
    parser.add_argument("--shake-threshold", type=float, default=SHAKE_THRESHOLD,
                        help="Shake strength needed for a pulse in m/s^2 (default: 4.0)")
    args = parser.parse_args()
    if not math.isfinite(args.shake_threshold) or args.shake_threshold <= 0:
        parser.error("--shake-threshold must be a positive number")

    pygame.mixer.pre_init(frequency=22050, size=-16, channels=1, buffer=512)
    pygame.init()
    screen = pygame.display.set_mode((WIDTH, HEIGHT))
    pygame.display.set_caption("Gyro Forge - ESP32 MPU6050")
    clock = pygame.time.Clock()
    game = Forge()
    renderer = Renderer()
    audio = Audio()
    controller = TiltController(args.port)
    running = True
    try:
        while running:
            dt = clock.tick(FPS) / 1000.0
            pulse = False
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        running = False
                    elif event.key == pygame.K_c:
                        controller.center()
                    elif event.key == pygame.K_m:
                        audio.muted = not audio.muted
                    elif event.key == pygame.K_p and game.status == "playing":
                        game.paused = not game.paused
                    elif event.key == pygame.K_r or (event.key == pygame.K_RETURN and game.status != "playing"):
                        game.reset()
                    elif event.key == pygame.K_SPACE:
                        pulse = True
            controller.update()
            keys = pygame.key.get_pressed()
            tilt_x = deadzone(controller.roll) * (-1 if args.invert_roll else 1) if controller.live else 0.0
            tilt_y = -deadzone(controller.pitch) * (-1 if args.invert_pitch else 1) if controller.live else 0.0
            yaw = controller.yaw_rate * (-1 if args.invert_yaw else 1) if controller.motion_live else 0.0
            if controller.motion_live and controller.shock >= args.shake_threshold:
                if game.status != "playing":
                    game.reset()
                elif game.paused:
                    game.paused = False
                else:
                    pulse = True
            if keys[pygame.K_LEFT] or keys[pygame.K_a]:
                tilt_x = -1.0
            if keys[pygame.K_RIGHT] or keys[pygame.K_d]:
                tilt_x = 1.0
            if keys[pygame.K_UP] or keys[pygame.K_w]:
                tilt_y = -1.0
            if keys[pygame.K_DOWN] or keys[pygame.K_s]:
                tilt_y = 1.0
            if keys[pygame.K_q]:
                yaw = -150.0
            if keys[pygame.K_e]:
                yaw = 150.0
            game.update(dt, tilt_x, tilt_y, yaw, pulse)
            for name in game.events:
                audio.play(name)
            renderer.draw(screen, game, controller)
            pygame.display.flip()
    finally:
        controller.close()
        pygame.quit()


if __name__ == "__main__":
    main()
