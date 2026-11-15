"""Prism Sync 3D: match a target cube with MPU6050 tilt, twist, and shake."""

from __future__ import annotations

import argparse
import math
import random

import pygame

from game import TiltController, clamp
from salvage import Audio


WIDTH, HEIGHT = 1100, 720
FPS = 60
ROUNDS = 6
ROUND_SECONDS = 105.0
SHAKE_THRESHOLD = 4.0
WHITE = (234, 247, 255)
MUTED = (133, 164, 185)
CYAN = (77, 229, 241)
GOLD = (255, 210, 96)
GREEN = (114, 239, 169)
RED = (255, 101, 125)


def angle_error(current: float, target: float) -> float:
    """Signed shortest difference, useful across the -180/180 boundary."""
    return (current - target + 180) % 360 - 180


class PrismGame:
    def __init__(self, seed: int | None = None) -> None:
        self.rng = random.Random(seed)
        self.reset()

    def reset(self) -> None:
        self.roll = 0.0
        self.pitch = 0.0
        self.yaw = 0.0
        self.round = 1
        self.score = 0
        self.remaining = ROUND_SECONDS
        self.elapsed = 0.0
        self.lock_cooldown = 0.0
        self.flash = 0.0
        self.message = "Match the ghost cube, then shake to lock"
        self.message_time = 5.0
        self.paused = False
        self.status = "playing"
        self.events: list[str] = []
        self._new_target()

    def _new_target(self) -> None:
        options = (-28, -18, -9, 0, 9, 18, 28)
        for _ in range(20):
            self.target_roll = float(self.rng.choice(options))
            self.target_pitch = float(self.rng.choice(options))
            self.target_yaw = float(self.rng.choice((-75, -50, -25, 0, 25, 50, 75)))
            distance = (abs(self.roll - self.target_roll) + abs(self.pitch - self.target_pitch)
                        + abs(angle_error(self.yaw, self.target_yaw)))
            if distance >= 35:
                return
        self.target_yaw = 75.0 if self.yaw < 0 else -75.0

    @property
    def tolerance(self) -> float:
        return 11.0 if self.round <= 2 else 9.0

    @property
    def errors(self) -> tuple[float, float, float]:
        return (self.roll - self.target_roll,
                self.pitch - self.target_pitch,
                angle_error(self.yaw, self.target_yaw))

    @property
    def aligned(self) -> bool:
        return all(abs(error) <= self.tolerance for error in self.errors)

    @property
    def alignment(self) -> float:
        roll, pitch, yaw = self.errors
        # The visual meter is a hint; lock still checks each axis independently.
        return clamp(1.0 - (abs(roll) + abs(pitch) + abs(yaw) * 0.55) / 120.0, 0.0, 1.0)

    def update(self, dt: float, roll_input: float, pitch_input: float,
               yaw_rate: float, lock: bool, sensor_absolute: bool) -> None:
        dt = clamp(dt, 0.0, 0.05)
        self.events.clear()
        if self.paused or self.status != "playing":
            return
        self.elapsed += dt
        self.remaining = max(0.0, self.remaining - dt)
        self.lock_cooldown = max(0.0, self.lock_cooldown - dt)
        self.flash = max(0.0, self.flash - dt)
        self.message_time = max(0.0, self.message_time - dt)
        if self.remaining == 0:
            self.status = "lost"
            self.events.append("lose")
            return

        if sensor_absolute:
            # The sketch already filters roll/pitch; blend over one frame for UI smoothness.
            blend = min(1.0, 12.0 * dt)
            self.roll += (clamp(roll_input, -38, 38) - self.roll) * blend
            self.pitch += (clamp(pitch_input, -38, 38) - self.pitch) * blend
        else:
            self.roll = clamp(self.roll + clamp(roll_input, -1, 1) * 74 * dt, -38, 38)
            self.pitch = clamp(self.pitch + clamp(pitch_input, -1, 1) * 74 * dt, -38, 38)
        if abs(yaw_rate) >= 3.0:
            self.yaw = angle_error(self.yaw + clamp(yaw_rate, -300, 300) * dt, 0)

        if lock and self.lock_cooldown == 0:
            self.lock_cooldown = 0.8
            if self.aligned:
                self.score += 200 + 50 * self.round + math.ceil(self.remaining / 10)
                self.flash = 0.6
                self.events.append("pickup")
                if self.round == ROUNDS:
                    self.status = "won"
                    self.events.append("win")
                else:
                    self.round += 1
                    self._new_target()
                    self.message = "Crystal synchronized! Match the next pose."
                    self.message_time = 2.5
            else:
                self.remaining = max(0.0, self.remaining - 1.0)
                self.message = "Not aligned yet. Check the three axis guides."
                self.message_time = 2.0
                self.events.append("hit")


VERTICES = [(x, y, z) for x in (-1.0, 1.0) for y in (-1.0, 1.0) for z in (-1.0, 1.0)]
FACES = (
    ((0, 1, 3, 2), (-1, 0, 0), (45, 135, 177)),
    ((4, 6, 7, 5), (1, 0, 0), (74, 192, 192)),
    ((0, 4, 5, 1), (0, -1, 0), (76, 104, 173)),
    ((2, 3, 7, 6), (0, 1, 0), (128, 107, 188)),
    ((0, 2, 6, 4), (0, 0, -1), (47, 94, 123)),
    ((1, 5, 7, 3), (0, 0, 1), (88, 205, 215)),
)


def rotate(point: tuple[float, float, float], roll: float, pitch: float, yaw: float) -> tuple[float, float, float]:
    """Apply X pitch, Y roll, then Z yaw to a 3D point."""
    x, y, z = point
    a, b, c = map(math.radians, (roll, pitch, yaw))
    y, z = y * math.cos(b) - z * math.sin(b), y * math.sin(b) + z * math.cos(b)
    x, z = x * math.cos(a) + z * math.sin(a), -x * math.sin(a) + z * math.cos(a)
    x, y = x * math.cos(c) - y * math.sin(c), x * math.sin(c) + y * math.cos(c)
    return x, y, z


class Renderer:
    def __init__(self) -> None:
        self.title = pygame.font.SysFont("consolas", 34, bold=True)
        self.heading = pygame.font.SysFont("consolas", 22, bold=True)
        self.small = pygame.font.SysFont("consolas", 16)
        self.big = pygame.font.SysFont("consolas", 51, bold=True)
        self.background = pygame.Surface((WIDTH, HEIGHT))
        for y in range(HEIGHT):
            mix = y / HEIGHT
            pygame.draw.line(self.background, (int(7 + 7 * mix), int(14 + 11 * mix), int(34 + 20 * mix)),
                             (0, y), (WIDTH, y))
        rng = random.Random(3)
        for _ in range(180):
            pygame.draw.circle(self.background, (37, 65, 88),
                               (rng.randrange(WIDTH), rng.randrange(HEIGHT)), rng.choice((1, 1, 2)))

    @staticmethod
    def _project(point: tuple[float, float, float], center: tuple[int, int], scale: float) -> tuple[int, int]:
        x, y, z = point
        factor = scale / (4.8 - z)
        return round(center[0] + x * factor), round(center[1] - y * factor)

    def _cube(self, surface: pygame.Surface, center: tuple[int, int],
              roll: float, pitch: float, yaw: float, target: bool) -> None:
        # A shared viewing angle reveals depth even when the controller is level.
        transformed = [rotate(vertex, roll + 23, pitch - 17, yaw) for vertex in VERTICES]
        normals = [rotate(normal, roll + 23, pitch - 17, yaw) for _, normal, _ in FACES]
        projected = [self._project(point, center, 540) for point in transformed]
        faces = []
        for (indices, _, color), normal in zip(FACES, normals):
            if normal[2] > 0.015:
                depth = sum(transformed[index][2] for index in indices) / 4
                faces.append((depth, indices, color, normal[2]))
        for _, indices, base, light in sorted(faces):
            points = [projected[index] for index in indices]
            if target:
                color = (int(72 + 83 * light), int(59 + 77 * light), int(42 + 35 * light))
                border = GOLD
            else:
                color = tuple(int(channel * (0.68 + 0.32 * light)) for channel in base)
                border = (159, 248, 251)
            pygame.draw.polygon(surface, color, points)
            pygame.draw.polygon(surface, border, points, 3)
            centroid = pygame.Vector2(sum(p[0] for p in points) / 4, sum(p[1] for p in points) / 4)
            inset = [pygame.Vector2(p).lerp(centroid, 0.18) for p in points]
            pygame.draw.polygon(surface, (24, 51, 67) if not target else (74, 61, 45), inset, 2)
            if light > 0.5:
                pygame.draw.circle(surface, border, centroid, 8, 2)

    def _axis_guide(self, surface: pygame.Surface, y: int, label: str,
                    current: float, target: float, error: float, tolerance: float) -> None:
        color = GREEN if abs(error) <= tolerance else GOLD
        surface.blit(self.small.render(label, True, MUTED), (85, y))
        surface.blit(self.small.render(f"{current:+4.0f}°  ->  {target:+4.0f}°", True, color), (194, y))
        track = pygame.Rect(460, y + 6, 210, 8)
        pygame.draw.rect(surface, (30, 55, 74), track, border_radius=4)
        pygame.draw.rect(surface, color, (track.left, track.top, round(track.width * clamp(1 - abs(error) / 90, 0, 1)), 8),
                         border_radius=4)
        surface.blit(self.small.render("ALIGNED" if abs(error) <= tolerance else f"{abs(error):.0f}° TO GO", True, color),
                     (690, y))

    def draw(self, surface: pygame.Surface, game: PrismGame, controller: TiltController | None = None) -> None:
        surface.blit(self.background, (0, 0))
        pygame.draw.circle(surface, (24, 60, 80), (319, 318), 184, 2)
        pygame.draw.circle(surface, (77, 67, 43), (783, 318), 184, 2)
        pygame.draw.circle(surface, (31, 78, 94), (319, 318), 171, 1)
        pygame.draw.circle(surface, (97, 85, 50), (783, 318), 171, 1)
        pygame.draw.line(surface, (54, 75, 83), (550, 162), (550, 514), 1)
        self._cube(surface, (319, 318), game.roll, game.pitch, game.yaw, target=False)
        self._cube(surface, (783, 318), game.target_roll, game.target_pitch, game.target_yaw, target=True)
        surface.blit(self.heading.render("YOUR PRISM", True, CYAN), (221, 487))
        surface.blit(self.heading.render("TARGET", True, GOLD), (735, 487))
        if game.aligned and game.status == "playing":
            ready = self.heading.render("SHAKE TO LOCK!", True, GREEN)
            surface.blit(ready, ready.get_rect(center=(WIDTH // 2, 93)))
        if game.flash > 0:
            pygame.draw.circle(surface, GREEN, (783, 318), round(174 + 40 * (1 - game.flash / 0.6)), 4)

        top = pygame.Surface((WIDTH, 80), pygame.SRCALPHA)
        top.fill((7, 17, 30, 231))
        surface.blit(top, (0, 0))
        surface.blit(self.title.render("PRISM SYNC 3D", True, WHITE), (28, 16))
        surface.blit(self.heading.render(f"SYNC {game.round}/{ROUNDS}", True, CYAN), (398, 24))
        surface.blit(self.heading.render(f"SCORE {game.score:04d}", True, GOLD), (605, 24))
        surface.blit(self.heading.render(f"TIME {math.ceil(game.remaining):03d}", True,
                                         RED if game.remaining < 20 else WHITE), (900, 24))
        pygame.draw.rect(surface, (31, 53, 71), (67, 518, 966, 6), border_radius=3)
        pygame.draw.rect(surface, GREEN if game.aligned else CYAN,
                         (67, 518, round(966 * game.alignment), 6), border_radius=3)
        roll_error, pitch_error, yaw_error = game.errors
        self._axis_guide(surface, 541, "ROLL", game.roll, game.target_roll, roll_error, game.tolerance)
        self._axis_guide(surface, 570, "PITCH", game.pitch, game.target_pitch, pitch_error, game.tolerance)
        self._axis_guide(surface, 599, "TWIST", game.yaw, game.target_yaw, yaw_error, game.tolerance)
        status = game.message if game.message_time > 0 else "Match all three axes and shake to lock"
        surface.blit(self.small.render(status, True, GREEN if game.aligned else GOLD), (72, 640))
        if controller and controller.motion_live:
            hint = f"MPU6050 LIVE   shake {controller.shock:.1f} m/s²"
        elif controller and controller.live:
            hint = "TILT LIVE - re-upload sketch for twist/shake"
        else:
            hint = "KEYBOARD: WASD tilt  Q/E twist  Space lock"
        surface.blit(self.small.render(hint, True, MUTED), (72, 670))
        surface.blit(self.small.render("C CENTER   P PAUSE   R RESTART   M MUTE   ESC QUIT", True, MUTED), (600, 670))

        if game.paused or game.status != "playing":
            shade = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
            shade.fill((3, 10, 24, 185))
            surface.blit(shade, (0, 0))
            heading = "PAUSED" if game.paused else ("PRISM MASTERED" if game.status == "won" else "TIME EXPIRED")
            color = CYAN if game.paused or game.status == "won" else RED
            title = self.big.render(heading, True, color)
            surface.blit(title, title.get_rect(center=(WIDTH // 2, HEIGHT // 2 - 42)))
            detail = self.heading.render(f"{min(game.round, ROUNDS)}/{ROUNDS} poses  |  {game.score} points", True, WHITE)
            surface.blit(detail, detail.get_rect(center=(WIDTH // 2, HEIGHT // 2 + 18)))
            prompt = "P to resume" if game.paused else "Shake, R, or Enter to retry"
            label = self.small.render(prompt, True, MUTED)
            surface.blit(label, label.get_rect(center=(WIDTH // 2, HEIGHT // 2 + 64)))


def main() -> None:
    parser = argparse.ArgumentParser(description="Prism Sync 3D for ESP32 MPU6050")
    parser.add_argument("--port", help="Serial port, for example COM5 or /dev/ttyUSB0 (default: auto-detect)")
    parser.add_argument("--invert-roll", action="store_true", help="Reverse left/right tilt")
    parser.add_argument("--invert-pitch", action="store_true", help="Reverse up/down tilt")
    parser.add_argument("--invert-yaw", action="store_true", help="Reverse twist direction")
    parser.add_argument("--shake-threshold", type=float, default=SHAKE_THRESHOLD,
                        help="Shake strength needed to lock a pose in m/s^2 (default: 4.0)")
    args = parser.parse_args()
    if not math.isfinite(args.shake_threshold) or args.shake_threshold <= 0:
        parser.error("--shake-threshold must be a positive number")

    pygame.mixer.pre_init(frequency=22050, size=-16, channels=1, buffer=512)
    pygame.init()
    screen = pygame.display.set_mode((WIDTH, HEIGHT))
    pygame.display.set_caption("Prism Sync 3D - ESP32 MPU6050")
    clock = pygame.time.Clock()
    game = PrismGame()
    renderer = Renderer()
    audio = Audio()
    controller = TiltController(args.port)
    running = True
    try:
        while running:
            dt = clock.tick(FPS) / 1000.0
            lock = False
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
                        lock = True
            controller.update()
            keys = pygame.key.get_pressed()
            sensor_absolute = controller.live
            if sensor_absolute:
                roll = controller.roll * (-1 if args.invert_roll else 1)
                pitch = controller.pitch * (-1 if args.invert_pitch else 1)
            else:
                roll = float(bool(keys[pygame.K_d] or keys[pygame.K_RIGHT])) - float(bool(keys[pygame.K_a] or keys[pygame.K_LEFT]))
                pitch = float(bool(keys[pygame.K_w] or keys[pygame.K_UP])) - float(bool(keys[pygame.K_s] or keys[pygame.K_DOWN]))
            yaw = controller.yaw_rate * (-1 if args.invert_yaw else 1) if controller.motion_live else 0.0
            if keys[pygame.K_q]:
                yaw = -120.0
            if keys[pygame.K_e]:
                yaw = 120.0
            if controller.motion_live and controller.shock >= args.shake_threshold:
                if game.status != "playing":
                    game.reset()
                elif game.paused:
                    game.paused = False
                else:
                    lock = True
            game.update(dt, roll, pitch, yaw, lock, sensor_absolute)
            for event_name in game.events:
                audio.play(event_name)
            renderer.draw(screen, game, controller)
            pygame.display.flip()
    finally:
        controller.close()
        pygame.quit()


if __name__ == "__main__":
    main()
