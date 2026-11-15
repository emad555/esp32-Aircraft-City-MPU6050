"""Orbital Salvage: a tilt-controlled arcade game for ESP32 + MPU6050."""

from __future__ import annotations

import argparse
from array import array
from dataclasses import dataclass
import math
import random

import pygame

from game import TiltController, clamp, deadzone


WIDTH, HEIGHT = 1100, 720
FPS = 60
FIELD = pygame.Rect(42, 112, 1016, 545)
PLAYER_RADIUS = 17
TARGET_CORES = 15
ROUND_SECONDS = 90.0
WHITE = (226, 242, 255)
MUTED = (137, 162, 190)
CYAN = (67, 230, 241)
GOLD = (255, 206, 89)
RED = (255, 91, 117)


@dataclass
class Core:
    pos: pygame.Vector2
    phase: float


@dataclass
class Mine:
    pos: pygame.Vector2
    vel: pygame.Vector2
    radius: int
    phase: float


@dataclass
class Particle:
    pos: pygame.Vector2
    vel: pygame.Vector2
    life: float
    max_life: float
    color: tuple[int, int, int]


class Arena:
    """Game state and collisions, independent of serial hardware and drawing."""

    def __init__(self, seed: int | None = None) -> None:
        self.rng = random.Random(seed)
        self.reset()

    def reset(self) -> None:
        self.player = pygame.Vector2(FIELD.center)
        self.velocity = pygame.Vector2()
        self.health = 3
        self.score = 0
        self.caught = 0
        self.combo = 0
        self.combo_time = 0.0
        self.remaining = ROUND_SECONDS
        self.elapsed = 0.0
        self.invulnerable = 0.0
        self.status = "playing"
        self.paused = False
        self.events: list[str] = []
        self.particles: list[Particle] = []
        self.mines: list[Mine] = []
        self.cores: list[Core] = []
        for _ in range(3):
            self._spawn_mine()
        for _ in range(3):
            self._spawn_core()

    def _random_point(self, margin: int = 36) -> pygame.Vector2:
        return pygame.Vector2(
            self.rng.uniform(FIELD.left + margin, FIELD.right - margin),
            self.rng.uniform(FIELD.top + margin, FIELD.bottom - margin),
        )

    def _spawn_core(self) -> None:
        for _ in range(100):
            pos = self._random_point(40)
            if pos.distance_to(self.player) < 115:
                continue
            if any(pos.distance_to(m.pos) < m.radius + 55 for m in self.mines):
                continue
            if any(pos.distance_to(c.pos) < 85 for c in self.cores):
                continue
            self.cores.append(Core(pos, self.rng.random() * math.tau))
            return
        # A crowded field should still offer a collectible target.
        self.cores.append(Core(self._random_point(40), self.rng.random() * math.tau))

    def _spawn_mine(self) -> None:
        for _ in range(100):
            pos = self._random_point(60)
            if pos.distance_to(self.player) > 190 and all(pos.distance_to(m.pos) > 110 for m in self.mines):
                break
        direction = pygame.Vector2(1, 0).rotate(self.rng.uniform(0, 360))
        speed = self.rng.uniform(75, 115) + self.caught * 2.0
        self.mines.append(Mine(pos, direction * speed, self.rng.randint(18, 26), self.rng.random() * math.tau))

    def _burst(self, pos: pygame.Vector2, color: tuple[int, int, int], amount: int) -> None:
        for _ in range(amount):
            angle = self.rng.uniform(0, math.tau)
            speed = self.rng.uniform(45, 180)
            life = self.rng.uniform(0.25, 0.65)
            self.particles.append(Particle(pos.copy(), pygame.Vector2(math.cos(angle), math.sin(angle)) * speed,
                                           life, life, color))

    def update(self, dt: float, horizontal: float, vertical: float) -> None:
        dt = clamp(dt, 0.0, 0.05)
        self.events.clear()
        if self.paused or self.status != "playing":
            return

        self.elapsed += dt
        self.remaining = max(0.0, self.remaining - dt)
        self.invulnerable = max(0.0, self.invulnerable - dt)
        self.combo_time = max(0.0, self.combo_time - dt)
        if self.combo_time == 0:
            self.combo = 0
        if self.remaining == 0:
            self.status = "lost"
            self.events.append("lose")
            return

        desired = pygame.Vector2(horizontal, vertical)
        if desired.length_squared() > 1:
            desired.normalize_ip()
        desired *= 390
        self.velocity += (desired - self.velocity) * min(1.0, 6.5 * dt)
        self.player += self.velocity * dt
        left, right = FIELD.left + PLAYER_RADIUS, FIELD.right - PLAYER_RADIUS
        top, bottom = FIELD.top + PLAYER_RADIUS, FIELD.bottom - PLAYER_RADIUS
        if self.player.x < left or self.player.x > right:
            self.player.x = clamp(self.player.x, left, right)
            self.velocity.x = 0
        if self.player.y < top or self.player.y > bottom:
            self.player.y = clamp(self.player.y, top, bottom)
            self.velocity.y = 0

        for mine in self.mines:
            mine.pos += mine.vel * dt
            mine.phase += dt * 2.2
            if mine.pos.x < FIELD.left + mine.radius or mine.pos.x > FIELD.right - mine.radius:
                mine.pos.x = clamp(mine.pos.x, FIELD.left + mine.radius, FIELD.right - mine.radius)
                mine.vel.x *= -1
            if mine.pos.y < FIELD.top + mine.radius or mine.pos.y > FIELD.bottom - mine.radius:
                mine.pos.y = clamp(mine.pos.y, FIELD.top + mine.radius, FIELD.bottom - mine.radius)
                mine.vel.y *= -1

        for core in self.cores[:]:
            core.phase += dt * 2.5
            if self.player.distance_to(core.pos) <= PLAYER_RADIUS + 17:
                self.cores.remove(core)
                self.caught += 1
                self.combo = min(self.combo + 1, 5) if self.combo_time > 0 else 1
                self.combo_time = 4.0
                self.score += 100 * self.combo
                self._burst(core.pos, GOLD, 18)
                self.events.append("pickup")
                if self.caught >= TARGET_CORES:
                    self.status = "won"
                    self.events.append("win")
                    return
                if self.caught % 5 == 0:
                    self._spawn_mine()
                self._spawn_core()

        if self.invulnerable == 0:
            for mine in self.mines:
                if self.player.distance_to(mine.pos) < PLAYER_RADIUS + mine.radius - 4:
                    self.health -= 1
                    self.combo = 0
                    self.combo_time = 0
                    self.invulnerable = 1.6
                    self.velocity = (self.player - mine.pos).normalize() * 330 if self.player != mine.pos else pygame.Vector2()
                    self._burst(self.player, RED, 25)
                    self.events.append("hit")
                    if self.health <= 0:
                        self.status = "lost"
                        self.events.append("lose")
                    break

        for particle in self.particles:
            particle.pos += particle.vel * dt
            particle.vel *= max(0.0, 1.0 - dt * 2.5)
            particle.life -= dt
        self.particles = [p for p in self.particles if p.life > 0]


class Audio:
    def __init__(self) -> None:
        self.muted = False
        self.sounds: dict[str, pygame.mixer.Sound] = {}
        if pygame.mixer.get_init() is None:
            return
        sample_rate, sample_format, channels = pygame.mixer.get_init()
        if sample_format != -16:
            return
        for name, duration, start, end in (
            ("pickup", 0.17, 540, 900),
            ("hit", 0.31, 250, 90),
            ("win", 0.65, 420, 840),
            ("lose", 0.55, 380, 120),
        ):
            samples = array("h")
            for index in range(int(duration * sample_rate)):
                t = index / sample_rate
                frequency = start + (end - start) * t / duration
                envelope = min(1.0, t / 0.008, (duration - t) / 0.05)
                value = int(10500 * envelope * math.sin(math.tau * frequency * t))
                samples.extend([value] * channels)
            self.sounds[name] = pygame.mixer.Sound(buffer=samples.tobytes())

    def play(self, name: str) -> None:
        if not self.muted and name in self.sounds:
            self.sounds[name].play()


class Renderer:
    def __init__(self) -> None:
        self.title = pygame.font.SysFont("consolas", 35, bold=True)
        self.hud = pygame.font.SysFont("consolas", 23, bold=True)
        self.small = pygame.font.SysFont("consolas", 16)
        self.big = pygame.font.SysFont("consolas", 50, bold=True)
        self.background = pygame.Surface((WIDTH, HEIGHT))
        for y in range(HEIGHT):
            mix = y / HEIGHT
            pygame.draw.line(self.background, (int(7 + 7 * mix), int(17 + 12 * mix), int(39 + 19 * mix)),
                             (0, y), (WIDTH, y))
        stars = random.Random(114)
        for _ in range(145):
            x, y = stars.randrange(WIDTH), stars.randrange(HEIGHT)
            pygame.draw.circle(self.background, (49, 74, 105), (x, y), stars.choice((1, 1, 2)))
        for x in range(FIELD.left, FIELD.right + 1, 40):
            pygame.draw.line(self.background, (19, 45, 69), (x, FIELD.top), (x, FIELD.bottom))
        for y in range(FIELD.top, FIELD.bottom + 1, 40):
            pygame.draw.line(self.background, (19, 45, 69), (FIELD.left, y), (FIELD.right, y))
        pygame.draw.rect(self.background, (43, 110, 138), FIELD, 2, border_radius=10)
        for corner in (FIELD.topleft, FIELD.topright, FIELD.bottomleft, FIELD.bottomright):
            pygame.draw.circle(self.background, CYAN, corner, 4)

    def _text(self, surface: pygame.Surface, label: str, font: pygame.font.Font,
              color: tuple[int, int, int], x: int, y: int) -> None:
        surface.blit(font.render(label, True, color), (x, y))

    def draw(self, surface: pygame.Surface, arena: Arena, controller: TiltController | None,
             audio: Audio | None) -> None:
        surface.blit(self.background, (0, 0))
        tick = arena.elapsed
        for core in arena.cores:
            pos = (round(core.pos.x), round(core.pos.y))
            pulse = math.sin(core.phase) * 3
            pygame.draw.circle(surface, (38, 92, 105), pos, 31 + round(pulse), 2)
            pygame.draw.circle(surface, (51, 148, 157), pos, 22 + round(pulse), 2)
            pygame.draw.circle(surface, GOLD, pos, 13)
            pygame.draw.circle(surface, (255, 243, 179), pos, 7)
            pygame.draw.line(surface, (255, 238, 161), (pos[0] - 24, pos[1]), (pos[0] - 18, pos[1]), 2)
            pygame.draw.line(surface, (255, 238, 161), (pos[0] + 18, pos[1]), (pos[0] + 24, pos[1]), 2)

        for mine in arena.mines:
            x, y = round(mine.pos.x), round(mine.pos.y)
            radius = mine.radius
            pygame.draw.circle(surface, (101, 33, 62), (x, y), radius + 10, 2)
            for index in range(8):
                angle = index * math.tau / 8 + mine.phase * 0.25
                inner = pygame.Vector2(math.cos(angle), math.sin(angle)) * (radius - 1)
                outer = pygame.Vector2(math.cos(angle), math.sin(angle)) * (radius + 9)
                pygame.draw.line(surface, RED, (x + inner.x, y + inner.y), (x + outer.x, y + outer.y), 3)
            pygame.draw.circle(surface, (45, 25, 48), (x, y), radius)
            pygame.draw.circle(surface, RED, (x, y), radius, 3)
            pygame.draw.circle(surface, (255, 151, 161), (x, y), 6 + round(2 * math.sin(mine.phase)))

        for particle in arena.particles:
            fade = particle.life / particle.max_life
            color = tuple(int(c * fade) for c in particle.color)
            pygame.draw.circle(surface, color, (round(particle.pos.x), round(particle.pos.y)), max(1, round(4 * fade)))

        x, y = round(arena.player.x), round(arena.player.y)
        if arena.invulnerable == 0 or int(tick * 12) % 2 == 0:
            pygame.draw.circle(surface, (27, 94, 119), (x, y), 27, 2)
            pygame.draw.circle(surface, CYAN, (x, y), PLAYER_RADIUS + 4, 2)
            pygame.draw.polygon(surface, (31, 102, 131), [(x, y - 20), (x + 18, y + 13), (x, y + 7), (x - 18, y + 13)])
            pygame.draw.polygon(surface, CYAN, [(x, y - 20), (x + 18, y + 13), (x, y + 7), (x - 18, y + 13)], 2)
            pygame.draw.circle(surface, WHITE, (x, y), 5)
            if arena.velocity.length_squared() > 2500:
                pygame.draw.line(surface, GOLD, (x, y + 15), (x, y + 24 + int(5 * math.sin(tick * 23))), 3)

        self._text(surface, "ORBITAL SALVAGE", self.title, WHITE, 42, 21)
        self._text(surface, f"SCORE {arena.score:05d}", self.hud, GOLD, 433, 29)
        self._text(surface, f"CORES {arena.caught:02d}/{TARGET_CORES}", self.hud, CYAN, 659, 29)
        self._text(surface, f"TIME {math.ceil(arena.remaining):02d}", self.hud,
                   RED if arena.remaining < 15 else WHITE, 902, 29)
        pygame.draw.rect(surface, (28, 50, 72), (42, 79, 1016, 7), border_radius=3)
        pygame.draw.rect(surface, CYAN, (42, 79, round(1016 * arena.caught / TARGET_CORES), 7), border_radius=3)
        self._text(surface, "SHIELDS", self.small, MUTED, 43, 664)
        for index in range(3):
            pygame.draw.circle(surface, CYAN if index < arena.health else (43, 69, 88), (154 + index * 27, 676), 9, 2)
        if controller is None:
            status = "ESP32 or keyboard"
        else:
            status = controller.message
        self._text(surface, status[:45], self.small, CYAN if controller and controller.live else MUTED, 280, 665)
        self._text(surface, "TILT / WASD  MOVE   C  CENTER   P  PAUSE   R  RESTART   M  MUTE", self.small,
                   MUTED, 280, 688)
        if arena.combo > 1 and arena.status == "playing":
            self._text(surface, f"{arena.combo}x COMBO", self.hud, GOLD, 78, 129)

        if arena.paused or arena.status != "playing":
            shade = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
            shade.fill((3, 11, 28, 186))
            surface.blit(shade, (0, 0))
            heading = "PAUSED" if arena.paused else ("MISSION COMPLETE" if arena.status == "won" else "MISSION OVER")
            color = CYAN if arena.paused or arena.status == "won" else RED
            title = self.big.render(heading, True, color)
            surface.blit(title, title.get_rect(center=(WIDTH // 2, HEIGHT // 2 - 48)))
            detail = f"{arena.caught}/{TARGET_CORES} cores  |  {arena.score} points"
            subtitle = self.hud.render(detail, True, WHITE)
            surface.blit(subtitle, subtitle.get_rect(center=(WIDTH // 2, HEIGHT // 2 + 14)))
            prompt = "P to resume" if arena.paused else "R or Enter to play again"
            label = self.small.render(prompt, True, MUTED)
            surface.blit(label, label.get_rect(center=(WIDTH // 2, HEIGHT // 2 + 65)))


def main() -> None:
    parser = argparse.ArgumentParser(description="Orbital Salvage for ESP32 MPU6050")
    parser.add_argument("--port", help="Serial port, for example COM5 or /dev/ttyUSB0 (default: auto-detect)")
    parser.add_argument("--invert-roll", action="store_true", help="Reverse left/right tilt")
    parser.add_argument("--invert-pitch", action="store_true", help="Reverse up/down tilt")
    args = parser.parse_args()

    pygame.mixer.pre_init(frequency=22050, size=-16, channels=1, buffer=512)
    pygame.init()
    screen = pygame.display.set_mode((WIDTH, HEIGHT))
    pygame.display.set_caption("Orbital Salvage - ESP32 MPU6050")
    clock = pygame.time.Clock()
    arena = Arena()
    controller = TiltController(args.port)
    audio = Audio()
    renderer = Renderer()
    running = True
    try:
        while running:
            dt = clock.tick(FPS) / 1000.0
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
                    elif event.key == pygame.K_p and arena.status == "playing":
                        arena.paused = not arena.paused
                    elif event.key == pygame.K_r or (event.key == pygame.K_RETURN and arena.status != "playing"):
                        arena.reset()

            controller.update()
            keys = pygame.key.get_pressed()
            horizontal = deadzone(controller.roll) * (-1 if args.invert_roll else 1) if controller.live else 0.0
            # Screen Y increases downward; keep positive pitch moving up as in Skyline Flight.
            vertical = -deadzone(controller.pitch) * (-1 if args.invert_pitch else 1) if controller.live else 0.0
            if keys[pygame.K_LEFT] or keys[pygame.K_a]:
                horizontal = -1.0
            if keys[pygame.K_RIGHT] or keys[pygame.K_d]:
                horizontal = 1.0
            if keys[pygame.K_UP] or keys[pygame.K_w]:
                vertical = 1.0
            if keys[pygame.K_DOWN] or keys[pygame.K_s]:
                vertical = -1.0
            arena.update(dt, clamp(horizontal, -1, 1), clamp(vertical, -1, 1))
            for name in arena.events:
                audio.play(name)
            renderer.draw(screen, arena, controller, audio)
            pygame.display.flip()
    finally:
        controller.close()
        pygame.quit()


if __name__ == "__main__":
    main()
