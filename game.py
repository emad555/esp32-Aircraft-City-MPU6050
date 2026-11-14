"""Skyline Flight: steer an aircraft through a city with an ESP32/MPU6050."""

from __future__ import annotations

import argparse
from array import array
import math
import random
import sys
import time
from dataclasses import dataclass

import pygame

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    serial = None
    list_ports = None


WIDTH, HEIGHT = 1100, 720
FPS = 60
HORIZON = 285
FOCAL = 760
SKY_TOP = (15, 34, 71)
SKY_BOTTOM = (87, 157, 191)
WHITE = (235, 248, 255)
CYAN = (84, 232, 245)
GOLD = (255, 199, 78)
RED = (255, 99, 105)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def deadzone(value: float, threshold: float = 2.0) -> float:
    if abs(value) <= threshold:
        return 0.0
    return math.copysign((abs(value) - threshold) / (25.0 - threshold), value)


def parse_imu(line: str) -> tuple[float, float] | None:
    parts = line.strip().split(",")
    if len(parts) != 3 or parts[0] != "IMU":
        return None
    try:
        roll, pitch = float(parts[1]), float(parts[2])
    except ValueError:
        return None
    if not (math.isfinite(roll) and math.isfinite(pitch)):
        return None
    return clamp(roll, -45, 45), clamp(pitch, -45, 45)


class GameAudio:
    """Small synthesized effects; no downloaded sound files are needed."""

    def __init__(self) -> None:
        self.available = pygame.mixer.get_init() is not None
        self.muted = False
        self.effects: dict[str, pygame.mixer.Sound] = {}
        self.engine_channel = None
        if not self.available:
            return

        pygame.mixer.set_num_channels(8)
        self.effects["ring"] = self._sound(
            0.30,
            lambda t, d: 0.42 * math.sin(2 * math.pi * (620 * t + 280 * t * t / d))
            + 0.16 * math.sin(2 * math.pi * 1240 * t),
        )
        rng = random.Random(6050)
        self.effects["hit"] = self._sound(
            0.38,
            lambda t, d: 0.32 * math.sin(2 * math.pi * (190 * t - 90 * t * t / d))
            + 0.22 * (rng.random() * 2 - 1),
        )
        self.effects["game_over"] = self._sound(
            0.85,
            lambda t, d: 0.34 * math.sin(2 * math.pi * (440 * t - 170 * t * t / d))
            + 0.12 * math.sin(2 * math.pi * 110 * t),
        )
        self.effects["start"] = self._sound(
            0.35,
            lambda t, d: 0.34 * math.sin(2 * math.pi * (420 * t + 220 * t * t / d)),
        )
        self.engine = self._sound(
            1.0,
            lambda t, d: 0.23 * math.sin(2 * math.pi * 84 * t)
            + 0.10 * math.sin(2 * math.pi * 168 * t)
            + 0.05 * math.sin(2 * math.pi * 252 * t),
            fade=False,
        )
        self.engine_channel = pygame.mixer.Channel(0)
        self.engine_channel.play(self.engine, loops=-1)
        self.engine_channel.set_volume(0.12)

    @staticmethod
    def _sound(duration: float, voice, fade: bool = True) -> pygame.mixer.Sound:
        sample_rate, sample_format, channels = pygame.mixer.get_init()
        if sample_format != -16:
            raise pygame.error("Game audio needs 16-bit mixer output")
        samples = array("h")
        for index in range(int(duration * sample_rate)):
            t = index / sample_rate
            envelope = min(1.0, t / 0.008, (duration - t) / 0.04) if fade else 1.0
            value = int(clamp(voice(t, duration) * envelope, -1, 1) * 32767)
            samples.extend([value] * channels)
        return pygame.mixer.Sound(buffer=samples.tobytes())

    def play(self, event: str) -> None:
        if self.available and not self.muted:
            self.effects[event].play()

    def toggle_mute(self) -> None:
        self.muted = not self.muted
        if not self.available:
            return
        if self.muted:
            pygame.mixer.stop()
        else:
            self.engine_channel.play(self.engine, loops=-1)
            self.engine_channel.set_volume(0.12)

    def update(self, game_over: bool) -> None:
        if self.available and not self.muted:
            self.engine_channel.set_volume(0.0 if game_over else 0.12)


class TiltController:
    """Nonblocking USB serial reader; the game stays playable without hardware."""

    def __init__(self, port: str | None) -> None:
        self.requested_port = port
        self.connection = None
        self.port_name = ""
        self.buffer = bytearray()
        self.roll = 0.0
        self.pitch = 0.0
        self.last_packet = 0.0
        self.next_connect = 0.0
        self.message = "Searching for ESP32"

    def _ports(self) -> list[str]:
        if self.requested_port:
            return [self.requested_port]
        if list_ports is None:
            return []
        available = list(list_ports.comports())
        preferred = [p for p in available if p.vid in (0x303A, 0x10C4, 0x1A86, 0x0403)]
        return [p.device for p in preferred or available]

    def update(self) -> None:
        now = time.monotonic()
        if self.connection is None:
            if now < self.next_connect or serial is None:
                return
            self.next_connect = now + 3.0
            for port in self._ports():
                try:
                    self.connection = serial.Serial(port, 115200, timeout=0, write_timeout=0)
                    self.port_name = port
                    self.buffer.clear()
                    self.message = f"Connecting to {port}"
                    break
                except (OSError, serial.SerialException):
                    continue
            if self.connection is None:
                self.message = "No ESP32 connected — keyboard available"
            return

        try:
            waiting = self.connection.in_waiting
            if waiting:
                self.buffer.extend(self.connection.read(min(waiting, 2048)))
                if len(self.buffer) > 4096:
                    self.buffer = self.buffer[-2048:]
                while b"\n" in self.buffer:
                    raw, _, rest = self.buffer.partition(b"\n")
                    self.buffer = bytearray(rest)
                    line = raw.decode("ascii", errors="ignore")
                    sample = parse_imu(line)
                    if sample is not None:
                        self.roll, self.pitch = sample
                        self.last_packet = now
                        self.message = f"MPU6050 live on {self.port_name}"
                    elif line.startswith("ERROR,"):
                        self.message = line.strip()
            if self.last_packet and now - self.last_packet > 1.0:
                self.roll = self.pitch = 0.0
                self.message = f"Waiting for MPU6050 on {self.port_name}"
        except (OSError, serial.SerialException):
            self.close()
            self.message = "ESP32 disconnected — keyboard available"
            self.next_connect = now + 2.0

    @property
    def live(self) -> bool:
        return self.connection is not None and time.monotonic() - self.last_packet < 0.5

    def center(self) -> None:
        if self.connection is not None:
            try:
                self.connection.write(b"C")
                self.message = "Controller centered"
            except (OSError, serial.SerialException):
                self.close()

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None
        self.last_packet = 0.0
        self.roll = self.pitch = 0.0


@dataclass
class Ring:
    x: float
    y: float
    z: float
    resolved: bool = False


@dataclass
class Drone:
    x: float
    y: float
    z: float
    resolved: bool = False


class CityFlight:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.x = 0.0
        self.y = 12.0
        self.z = 0.0
        self.speed = 22.0
        self.health = 3
        self.score = 0
        self.combo = 0
        self.rings: list[Ring] = []
        self.drones: list[Drone] = []
        self.next_ring = 45.0
        self.next_drone = 115.0
        self.invincible = 0.0
        self.game_over = False
        self.roll_visual = 0.0
        self.events: list[str] = []
        self.spawn_ahead()

    def spawn_ahead(self) -> None:
        while self.next_ring < self.z + 230:
            index = len(self.rings) + int(self.next_ring / 70)
            self.rings.append(Ring(6.0 * math.sin(index * 1.9), 12.0 + 4.0 * math.sin(index * 1.3), self.next_ring))
            self.next_ring += 70.0
        while self.next_drone < self.z + 230:
            index = int(self.next_drone / 105)
            self.drones.append(Drone(6.5 * math.sin(index * 2.5 + 1), 11.0 + 4.0 * math.cos(index * 1.7), self.next_drone))
            self.next_drone += 105.0

    def hit(self) -> None:
        if self.invincible > 0:
            return
        self.health -= 1
        self.combo = 0
        self.invincible = 1.8
        self.x *= 0.45
        self.y = clamp(self.y, 8, 19)
        if self.health <= 0:
            self.game_over = True
            self.events.append("game_over")
        else:
            self.events.append("hit")

    def update(self, dt: float, horizontal: float, vertical: float) -> None:
        self.events.clear()
        if self.game_over:
            return
        self.invincible = max(0.0, self.invincible - dt)
        old_z = self.z
        self.x = clamp(self.x + horizontal * 19.0 * dt, -42, 42)
        self.y = clamp(self.y + vertical * 13.0 * dt, 4, 31)
        self.z += self.speed * dt
        self.speed = min(36.0, 22.0 + self.z / 320.0)
        self.roll_visual += (horizontal * 0.45 - self.roll_visual) * min(1, 8 * dt)
        self.spawn_ahead()

        for ring in self.rings:
            if not ring.resolved and old_z < ring.z <= self.z:
                ring.resolved = True
                if abs(self.x - ring.x) < 4.3 and abs(self.y - ring.y) < 4.3:
                    self.combo += 1
                    self.score += 100 * min(self.combo, 5)
                    self.events.append("ring")
                else:
                    self.combo = 0
        for drone in self.drones:
            if not drone.resolved and old_z < drone.z <= self.z:
                drone.resolved = True
                if abs(self.x - drone.x) < 3.2 and abs(self.y - drone.y) < 3.2:
                    self.hit()

        # Rows of buildings border the central avenue. Their heights vary by block.
        block = int(self.z // 32)
        if abs(self.z - (block * 32 + 16)) < 8:
            for side in (-1, 1):
                for lane in (0, 1):
                    cx = side * (19 + lane * 18)
                    width, height = building_shape(block, side, lane)
                    if abs(self.x - cx) < width / 2 + 1.5 and self.y < height + 1:
                        self.hit()
                        break

        self.rings = [r for r in self.rings if r.z > self.z - 12]
        self.drones = [d for d in self.drones if d.z > self.z - 12]


def building_shape(block: int, side: int, lane: int) -> tuple[float, float]:
    rng = random.Random(block * 10007 + (side + 2) * 1009 + lane * 131)
    return rng.uniform(11, 15), rng.uniform(12, 31)


def project(x: float, y: float, z: float, game: CityFlight) -> tuple[int, int] | None:
    depth = z - (game.z - 14)
    if depth <= 1:
        return None
    camera_x = game.x * 0.65
    camera_y = game.y + 4.3
    return (int(WIDTH / 2 + (x - camera_x) * FOCAL / depth),
            int(HORIZON - (y - camera_y) * FOCAL / depth))


def polygon_world(surface: pygame.Surface, color: tuple[int, int, int],
                  points: list[tuple[float, float, float]], game: CityFlight,
                  width: int = 0) -> None:
    projected = [project(*p, game) for p in points]
    if all(p is not None for p in projected):
        pygame.draw.polygon(surface, color, projected, width)


def draw_background(surface: pygame.Surface, game: CityFlight) -> None:
    for y in range(0, HEIGHT, 4):
        t = min(1.0, y / HORIZON)
        color = tuple(int(a + (b - a) * t) for a, b in zip(SKY_TOP, SKY_BOTTOM))
        pygame.draw.rect(surface, color, (0, y, WIDTH, 4))
    pygame.draw.circle(surface, (255, 220, 148), (885, 116), 42)
    pygame.draw.circle(surface, (255, 239, 189), (885, 116), 30)
    # The distant skyline sits behind the moving city.
    for i in range(33):
        rng = random.Random(i * 397)
        h = rng.randint(28, 105)
        x = i * 36 - 12
        pygame.draw.rect(surface, (64, 112, 145), (x, HORIZON - h, 29, h))
    pygame.draw.rect(surface, (45, 96, 103), (0, HORIZON, WIDTH, HEIGHT - HORIZON))

    start = int(game.z // 20) * 20
    for z in range(start + 220, start - 21, -20):
        polygon_world(surface, (60, 77, 80), [(-10, 0.04, z), (10, 0.04, z),
                                               (10, 0.04, z + 20), (-10, 0.04, z + 20)], game)
        for edge in (-10, 10):
            polygon_world(surface, (234, 185, 97), [(edge, 0.07, z), (edge + 0.18, 0.07, z),
                                                     (edge + 0.18, 0.07, z + 20), (edge, 0.07, z + 20)], game)
        polygon_world(surface, (165, 177, 168), [(-0.12, 0.08, z + 3), (0.12, 0.08, z + 3),
                                                   (0.12, 0.08, z + 11), (-0.12, 0.08, z + 11)], game)


def draw_building(surface: pygame.Surface, game: CityFlight, block: int, side: int, lane: int) -> None:
    cx = side * (19 + lane * 18)
    width, height = building_shape(block, side, lane)
    x0, x1 = cx - width / 2, cx + width / 2
    z0, z1 = block * 32 + 4, block * 32 + 28
    palette = [(80, 121, 145), (75, 104, 131), (96, 118, 133), (90, 107, 126)]
    base = palette[(block * 3 + lane + side) % len(palette)]
    front = tuple(min(255, c + 20) for c in base)
    shade = tuple(max(0, c - 22) for c in base)
    polygon_world(surface, shade, [(x0, 0, z0), (x0, height, z0),
                                   (x0, height, z1), (x0, 0, z1)], game)
    polygon_world(surface, base, [(x1, 0, z0), (x1, height, z0),
                                  (x1, height, z1), (x1, 0, z1)], game)
    polygon_world(surface, (151, 174, 171), [(x0, height, z0), (x1, height, z0),
                                              (x1, height, z1), (x0, height, z1)], game)
    polygon_world(surface, front, [(x0, 0, z0), (x1, 0, z0),
                                   (x1, height, z0), (x0, height, z0)], game)
    for row in range(2, int(height // 4)):
        for col in (0.25, 0.65):
            wx = x0 + width * col
            wy = row * 4
            window = (255, 209, 132) if (block + row + lane) % 4 == 0 else (102, 178, 195)
            polygon_world(surface, window, [(wx, wy, z0 - 0.03), (wx + 1.8, wy, z0 - 0.03),
                                             (wx + 1.8, wy + 1.8, z0 - 0.03), (wx, wy + 1.8, z0 - 0.03)], game)


def draw_ring(surface: pygame.Surface, game: CityFlight, ring: Ring) -> None:
    center = project(ring.x, ring.y, ring.z, game)
    if center is None:
        return
    depth = ring.z - (game.z - 14)
    radius = int(4.5 * FOCAL / depth)
    if radius < 2 or radius > 400:
        return
    rect = pygame.Rect(center[0] - radius, center[1] - radius,
                       radius * 2, radius * 2)
    pygame.draw.ellipse(surface, (88, 79, 37), rect, max(2, radius // 4))
    pygame.draw.ellipse(surface, GOLD, rect, max(2, radius // 7))
    pygame.draw.ellipse(surface, (255, 249, 202), rect.inflate(-radius // 5, -radius // 5),
                        max(1, radius // 18))


def draw_drone(surface: pygame.Surface, game: CityFlight, drone: Drone) -> None:
    p = project(drone.x, drone.y, drone.z, game)
    if p is None:
        return
    size = int(2.4 * FOCAL / (drone.z - game.z + 14))
    if size < 2 or size > 200:
        return
    x, y = p
    pygame.draw.line(surface, (52, 45, 58), (x - size, y), (x + size, y), max(2, size // 5))
    pygame.draw.circle(surface, RED, (x, y), max(3, size // 2))
    for side in (-1, 1):
        pygame.draw.circle(surface, (24, 34, 50), (x + side * size, y), max(3, size // 3), 2)


def draw_aircraft(surface: pygame.Surface, game: CityFlight) -> None:
    if game.invincible > 0 and int(game.invincible * 9) % 2 == 0:
        return
    p = project(game.x, game.y, game.z + 4, game)
    if p is None:
        return
    cx, cy = p
    angle = game.roll_visual

    def rotated(px: float, py: float) -> tuple[int, int]:
        return (int(cx + px * math.cos(angle) - py * math.sin(angle)),
                int(cy + px * math.sin(angle) + py * math.cos(angle)))

    shadow = [rotated(x, y + 10) for x, y in [(-82, 6), (-12, -4), (0, -50), (12, -4), (82, 6), (15, 14), (0, 26), (-15, 14)]]
    pygame.draw.polygon(surface, (19, 42, 57), shadow)
    wings = [rotated(x, y) for x, y in [(-92, 3), (-17, -11), (0, -50), (17, -11), (92, 3),
                                         (29, 13), (10, 8), (7, 30), (-7, 30), (-10, 8), (-29, 13)]]
    pygame.draw.polygon(surface, (221, 237, 235), wings)
    pygame.draw.polygon(surface, (77, 202, 219), [rotated(-92, 3), rotated(-71, -1), rotated(-63, 12), rotated(-89, 12)])
    pygame.draw.polygon(surface, (77, 202, 219), [rotated(92, 3), rotated(71, -1), rotated(63, 12), rotated(89, 12)])
    pygame.draw.polygon(surface, (50, 83, 110), [rotated(-11, -13), rotated(0, -41), rotated(11, -13), rotated(7, 9), rotated(-7, 9)])
    pygame.draw.polygon(surface, (255, 209, 101), [rotated(-4, 22), rotated(4, 22), rotated(0, 47)])
    pygame.draw.lines(surface, (29, 62, 83), True, wings, 2)


def draw_hud(surface: pygame.Surface, game: CityFlight, controller: TiltController, audio: GameAudio,
             font: pygame.font.Font, small: pygame.font.Font, big: pygame.font.Font) -> None:
    panel = pygame.Surface((WIDTH, 92), pygame.SRCALPHA)
    panel.fill((9, 22, 42, 198))
    surface.blit(panel, (0, 0))
    surface.blit(big.render("SKYLINE FLIGHT", True, WHITE), (28, 14))
    surface.blit(small.render("ESP32  /  MPU6050", True, CYAN), (31, 58))
    surface.blit(font.render(f"SCORE  {game.score:05d}", True, GOLD), (390, 27))
    surface.blit(font.render(f"DISTANCE  {int(game.z):04d} m", True, WHITE), (630, 27))
    surface.blit(font.render("♥" * game.health, True, RED), (935, 25))

    status = controller.message
    if controller.live:
        status += f"   R {controller.roll:+.0f}°   P {controller.pitch:+.0f}°"
    bottom = pygame.Surface((WIDTH, 62), pygame.SRCALPHA)
    bottom.fill((9, 22, 42, 188))
    surface.blit(bottom, (0, HEIGHT - 62))
    surface.blit(small.render(status[:95], True, CYAN if controller.live else WHITE), (24, HEIGHT - 55))
    sound_label = "sound off" if audio.muted else "sound on" if audio.available else "audio unavailable"
    surface.blit(small.render(f"Tilt / arrows: steer & climb   C: center   R: restart   M: {sound_label}   Esc: quit", True, WHITE),
                 (24, HEIGHT - 30))

    if game.combo > 1:
        surface.blit(font.render(f"{game.combo}× COMBO", True, GOLD), (WIDTH // 2 - 65, 120))
    if game.game_over:
        dim = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
        dim.fill((8, 16, 32, 180))
        surface.blit(dim, (0, 0))
        title = big.render("FLIGHT OVER", True, WHITE)
        detail = font.render(f"Score {game.score}   •   Distance {int(game.z)} m", True, GOLD)
        prompt = font.render("Press R or Enter to fly again", True, CYAN)
        surface.blit(title, title.get_rect(center=(WIDTH // 2, HEIGHT // 2 - 55)))
        surface.blit(detail, detail.get_rect(center=(WIDTH // 2, HEIGHT // 2 + 2)))
        surface.blit(prompt, prompt.get_rect(center=(WIDTH // 2, HEIGHT // 2 + 54)))


def draw(surface: pygame.Surface, game: CityFlight, controller: TiltController, audio: GameAudio,
         font: pygame.font.Font, small: pygame.font.Font, big: pygame.font.Font) -> None:
    draw_background(surface, game)
    first = int((game.z - 20) // 32)
    last = int((game.z + 220) // 32)
    for block in range(last, first - 1, -1):
        for lane in (1, 0):
            for side in (-1, 1):
                draw_building(surface, game, block, side, lane)
    for ring in sorted(game.rings, key=lambda r: r.z, reverse=True):
        if not ring.resolved and ring.z > game.z:
            draw_ring(surface, game, ring)
    for drone in sorted(game.drones, key=lambda d: d.z, reverse=True):
        if not drone.resolved and drone.z > game.z:
            draw_drone(surface, game, drone)
    draw_aircraft(surface, game)
    draw_hud(surface, game, controller, audio, font, small, big)


def main() -> None:
    parser = argparse.ArgumentParser(description="ESP32 MPU6050 aircraft city game")
    parser.add_argument("--port", help="Serial port, for example COM5 or /dev/ttyUSB0 (default: auto-detect)")
    parser.add_argument("--invert-roll", action="store_true", help="Reverse left/right sensor steering")
    parser.add_argument("--invert-pitch", action="store_true", help="Reverse climb/dive sensor steering")
    args = parser.parse_args()

    pygame.mixer.pre_init(frequency=22050, size=-16, channels=1, buffer=512)
    pygame.init()
    screen = pygame.display.set_mode((WIDTH, HEIGHT))
    pygame.display.set_caption("Skyline Flight — ESP32 MPU6050")
    clock = pygame.time.Clock()
    font = pygame.font.SysFont("consolas", 25, bold=True)
    small = pygame.font.SysFont("consolas", 16)
    big = pygame.font.SysFont("consolas", 38, bold=True)
    game = CityFlight()
    controller = TiltController(args.port)
    audio = GameAudio()
    audio.play("start")
    running = True

    try:
        while running:
            dt = min(clock.tick(FPS) / 1000.0, 0.05)
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        running = False
                    elif event.key == pygame.K_c:
                        controller.center()
                    elif event.key == pygame.K_m:
                        audio.toggle_mute()
                    elif event.key == pygame.K_r or (event.key == pygame.K_RETURN and game.game_over):
                        game.reset()
                        audio.play("start")

            controller.update()
            keys = pygame.key.get_pressed()
            horizontal = (deadzone(controller.roll) * (-1 if args.invert_roll else 1)) if controller.live else 0.0
            vertical = (deadzone(controller.pitch) * (-1 if args.invert_pitch else 1)) if controller.live else 0.0
            if keys[pygame.K_LEFT] or keys[pygame.K_a]:
                horizontal = -1.0
            if keys[pygame.K_RIGHT] or keys[pygame.K_d]:
                horizontal = 1.0
            if keys[pygame.K_UP] or keys[pygame.K_w]:
                vertical = 1.0
            if keys[pygame.K_DOWN] or keys[pygame.K_s]:
                vertical = -1.0
            game.update(dt, clamp(horizontal, -1, 1), clamp(vertical, -1, 1))
            for sound_event in game.events:
                audio.play(sound_event)
            audio.update(game.game_over)
            draw(screen, game, controller, audio, font, small, big)
            pygame.display.flip()
    finally:
        controller.close()
        pygame.quit()


if __name__ == "__main__":
    main()
