"""Neon City Escape: a top-down driving mission for ESP32 + MPU6050."""

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
WORLD = pygame.Rect(0, 0, 2700, 2400)
ROAD_X = (300, 820, 1340, 1860, 2380)
ROAD_Y = (300, 750, 1200, 1650, 2100)
ROAD_WIDTH = 150
MAX_HEALTH = 5
PACKAGES = ((1340, 750), (1860, 1200), (1340, 1650))
GARAGE = pygame.Vector2(2380, 2100)
WHITE = (235, 244, 255)
MUTED = (156, 174, 190)
CYAN = (76, 228, 245)
GOLD = (255, 204, 82)
RED = (255, 91, 111)
GREEN = (100, 239, 158)


@dataclass
class Car:
    pos: pygame.Vector2
    angle: float
    color: tuple[int, int, int]
    target: bool = False
    speed: float = 0.0


@dataclass
class Police:
    pos: pygame.Vector2
    node: tuple[int, int]
    next_node: tuple[int, int]
    angle: float = 0.0


def nearest_node(pos: pygame.Vector2) -> tuple[int, int]:
    return min(range(len(ROAD_X)), key=lambda i: abs(ROAD_X[i] - pos.x)), min(
        range(len(ROAD_Y)), key=lambda j: abs(ROAD_Y[j] - pos.y)
    )


def node_pos(node: tuple[int, int]) -> pygame.Vector2:
    return pygame.Vector2(ROAD_X[node[0]], ROAD_Y[node[1]])


def road_blocks() -> list[tuple[pygame.Rect, str]]:
    blocks: list[tuple[pygame.Rect, str]] = []
    for i in range(len(ROAD_X) - 1):
        for j in range(len(ROAD_Y) - 1):
            left = ROAD_X[i] + ROAD_WIDTH // 2 + 23
            top = ROAD_Y[j] + ROAD_WIDTH // 2 + 23
            right = ROAD_X[i + 1] - ROAD_WIDTH // 2 - 23
            bottom = ROAD_Y[j + 1] - ROAD_WIDTH // 2 - 23
            kind = "park" if (i * 3 + j * 2) % 7 == 0 else "building"
            blocks.append((pygame.Rect(left, top, right - left, bottom - top), kind))
    return blocks


BLOCKS = road_blocks()


class City:
    """Mission and movement state. Coordinates are in a shared scrolling city."""

    def __init__(self, seed: int | None = None) -> None:
        self.rng = random.Random(seed)
        self.reset()

    def reset(self) -> None:
        self.player = pygame.Vector2(820, 820)
        self.current_car: Car | None = None
        self.cars = [
            Car(pygame.Vector2(820, 750), 90, GOLD, target=True),
            Car(pygame.Vector2(300, 750), 0, (89, 188, 210)),
            Car(pygame.Vector2(1340, 300), 180, (188, 115, 210)),
            Car(pygame.Vector2(1860, 1650), 270, (106, 213, 144)),
            Car(pygame.Vector2(820, 2100), 90, (206, 139, 99)),
        ]
        self.police: list[Police] = []
        self.phase = "steal"
        self.package_index = 0
        self.wanted = 0
        self.health = MAX_HEALTH
        self.cash = 0
        self.hit_cooldown = 0.0
        self.elapsed = 0.0
        self.paused = False
        self.message = "Walk to the gold car and press E to enter"
        self.message_time = 5.0
        self.events: list[str] = []

    @property
    def objective(self) -> pygame.Vector2:
        if self.phase == "steal":
            return self.cars[0].pos.copy()
        if self.phase == "collect":
            return pygame.Vector2(PACKAGES[self.package_index])
        return GARAGE.copy()

    @property
    def objective_label(self) -> str:
        if self.phase == "steal":
            return "STEAL THE GOLD CAR"
        if self.phase == "collect":
            return f"COLLECT PACKAGE {self.package_index + 1}/{len(PACKAGES)}"
        if self.phase == "escape":
            return "REACH THE GREEN GARAGE"
        return "MISSION COMPLETE" if self.phase == "won" else "BUSTED"

    def _blocked(self, pos: pygame.Vector2, radius: float) -> bool:
        if not (radius <= pos.x <= WORLD.width - radius and radius <= pos.y <= WORLD.height - radius):
            return True
        for rect, kind in BLOCKS:
            if kind != "building":
                continue
            nearest_x = clamp(pos.x, rect.left, rect.right)
            nearest_y = clamp(pos.y, rect.top, rect.bottom)
            if (pos.x - nearest_x) ** 2 + (pos.y - nearest_y) ** 2 < radius ** 2:
                return True
        return False

    def _spawn_police(self) -> None:
        choices = [(i, j) for i in range(len(ROAD_X)) for j in range(len(ROAD_Y))
                   if node_pos((i, j)).distance_to(self.player) > 650]
        if not choices:
            return
        node = self.rng.choice(choices)
        self.police.append(Police(node_pos(node), node, node))

    def interact(self) -> None:
        if self.phase in ("won", "lost") or self.paused:
            return
        if self.current_car is None:
            nearby = min(self.cars, key=lambda car: car.pos.distance_to(self.player))
            if nearby.pos.distance_to(self.player) > 75:
                self.message = "Move closer to a car, then press E"
                self.message_time = 2.5
                return
            self.current_car = nearby
            self.player = nearby.pos.copy()
            if self.phase == "steal" and nearby.target:
                self.phase = "collect"
                self.wanted = 2
                self.message = "Car acquired. Follow the gold waypoint!"
                for _ in range(2):
                    self._spawn_police()
            elif self.wanted == 0:
                self.wanted = 1
                self._spawn_police()
                self.message = "Car taken. Find the gold mission car."
            else:
                self.message = "Vehicle switched"
            self.message_time = 3.5
        else:
            car = self.current_car
            heading = pygame.Vector2(math.sin(math.radians(car.angle)), -math.cos(math.radians(car.angle)))
            side = pygame.Vector2(-heading.y, heading.x)
            for direction in (1, -1):
                candidate = car.pos + side * 43 * direction
                if not self._blocked(candidate, 13):
                    self.player = candidate
                    self.current_car = None
                    car.speed = 0.0
                    self.message = "On foot. Press E near a car to enter."
                    self.message_time = 2.5
                    return
            self.message = "No room to exit here"
            self.message_time = 2.5

    def _police_step(self, unit: Police, dt: float) -> None:
        target = nearest_node(self.player)
        if unit.node == target and unit.next_node == target:
            chase = self.player - unit.pos
            if chase.length_squared() > 100:
                direction = chase.normalize()
                candidate = unit.pos + direction * (210 + 14 * self.wanted) * dt
                if not self._blocked(candidate, 20):
                    unit.pos = candidate
                    unit.angle = math.degrees(math.atan2(direction.x, -direction.y))
            return
        waypoint = node_pos(unit.next_node)
        delta = waypoint - unit.pos
        if delta.length_squared() < 100:
            unit.pos = waypoint
            unit.node = unit.next_node
            if unit.node == target:
                unit.next_node = unit.node
                return
            i, j = unit.node
            neighbors = [(i + di, j + dj) for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1))
                         if 0 <= i + di < len(ROAD_X) and 0 <= j + dj < len(ROAD_Y)]
            neighbors.sort(key=lambda n: abs(n[0] - target[0]) + abs(n[1] - target[1]))
            unit.next_node = neighbors[0]
            waypoint = node_pos(unit.next_node)
            delta = waypoint - unit.pos
        if delta.length_squared() > 0:
            direction = delta.normalize()
            unit.pos += direction * min(delta.length(), (210 + 14 * self.wanted) * dt)
            unit.angle = math.degrees(math.atan2(direction.x, -direction.y))

    def _damage(self, text: str) -> None:
        if self.hit_cooldown > 0:
            return
        self.health -= 1
        self.hit_cooldown = 2.0
        self.message = text
        self.message_time = 2.4
        self.events.append("hit")
        if self.health <= 0:
            self.phase = "lost"
            self.events.append("lose")

    def update(self, dt: float, horizontal: float, vertical: float, handbrake: bool = False) -> None:
        dt = clamp(dt, 0.0, 0.05)
        self.events.clear()
        if self.paused or self.phase in ("won", "lost"):
            return
        self.elapsed += dt
        self.hit_cooldown = max(0.0, self.hit_cooldown - dt)
        self.message_time = max(0.0, self.message_time - dt)
        horizontal = clamp(horizontal, -1, 1)
        vertical = clamp(vertical, -1, 1)

        if self.current_car is None:
            movement = pygame.Vector2(horizontal, vertical)
            if movement.length_squared() > 1:
                movement.normalize_ip()
            movement *= 220 * dt
            candidate_x = pygame.Vector2(self.player.x + movement.x, self.player.y)
            if not self._blocked(candidate_x, 13):
                self.player.x = candidate_x.x
            candidate_y = pygame.Vector2(self.player.x, self.player.y + movement.y)
            if not self._blocked(candidate_y, 13):
                self.player.y = candidate_y.y
        else:
            car = self.current_car
            throttle = -vertical
            car.speed += throttle * 340 * dt
            if abs(throttle) < 0.05:
                car.speed *= max(0.0, 1.0 - (5.0 if handbrake else 1.6) * dt)
            if handbrake:
                car.speed *= max(0.0, 1.0 - 3.0 * dt)
            car.speed = clamp(car.speed, -140, 390)
            turn_factor = min(1.0, max(0.23, abs(car.speed) / 190))
            car.angle += horizontal * 150 * dt * turn_factor * (1 if car.speed >= 0 else -1)
            heading = pygame.Vector2(math.sin(math.radians(car.angle)), -math.cos(math.radians(car.angle)))
            candidate = car.pos + heading * car.speed * dt
            if self._blocked(candidate, 23):
                if abs(car.speed) > 170:
                    self._damage("Vehicle damaged by a building")
                car.speed *= -0.15
            else:
                car.pos = candidate
            self.player = car.pos.copy()

        for unit in self.police:
            self._police_step(unit, dt)
            if unit.pos.distance_to(self.player) < (33 if self.current_car else 25):
                self._damage("Police rammed your vehicle" if self.current_car else "Police caught you on foot")

        if self.phase == "collect" and self.current_car and self.player.distance_to(self.objective) < 66:
            self.package_index += 1
            self.cash += 500
            self.wanted = min(5, self.wanted + 1)
            self.events.append("pickup")
            if len(self.police) < self.wanted:
                self._spawn_police()
            if self.package_index == len(PACKAGES):
                self.phase = "escape"
                self.message = "All packages collected. Reach the green garage!"
            else:
                self.message = f"Package secured. Head to stop {self.package_index + 1}."
            self.message_time = 4.0
        elif self.phase == "escape" and self.current_car and self.player.distance_to(GARAGE) < 80:
            self.phase = "won"
            self.events.append("win")


class Renderer:
    def __init__(self) -> None:
        self.title = pygame.font.SysFont("consolas", 33, bold=True)
        self.hud = pygame.font.SysFont("consolas", 20, bold=True)
        self.small = pygame.font.SysFont("consolas", 16)
        self.big = pygame.font.SysFont("consolas", 52, bold=True)

    @staticmethod
    def _screen(pos: pygame.Vector2, camera: pygame.Vector2) -> tuple[int, int]:
        return round(pos.x - camera.x), round(pos.y - camera.y)

    def _car(self, surface: pygame.Surface, pos: pygame.Vector2, angle: float,
             color: tuple[int, int, int], camera: pygame.Vector2, police: bool = False) -> None:
        center = pygame.Vector2(self._screen(pos, camera))
        forward = pygame.Vector2(math.sin(math.radians(angle)), -math.cos(math.radians(angle)))
        side = pygame.Vector2(-forward.y, forward.x)
        corners = [center + forward * 25 + side * 14, center + forward * 25 - side * 14,
                   center - forward * 25 - side * 14, center - forward * 25 + side * 14]
        pygame.draw.polygon(surface, (5, 15, 27), [(p.x + 3, p.y + 4) for p in corners])
        pygame.draw.polygon(surface, color, corners)
        pygame.draw.polygon(surface, (24, 36, 52), corners, 2)
        windshield = [center + forward * 8 + side * 11, center + forward * 8 - side * 11,
                      center - forward * 6 - side * 11, center - forward * 6 + side * 11]
        pygame.draw.polygon(surface, (27, 69, 90), windshield)
        for sign in (-1, 1):
            lamp = center + forward * 22 + side * 9 * sign
            pygame.draw.circle(surface, WHITE if not police else RED, lamp, 3)
        if police:
            pygame.draw.circle(surface, RED, center - side * 5, 4)
            pygame.draw.circle(surface, CYAN, center + side * 5, 4)

    def _world(self, surface: pygame.Surface, city: City, camera: pygame.Vector2) -> None:
        surface.fill((20, 31, 39))
        for y in ROAD_Y:
            rect = pygame.Rect(-camera.x, y - ROAD_WIDTH // 2 - camera.y, WORLD.width, ROAD_WIDTH)
            pygame.draw.rect(surface, (46, 54, 62), rect)
            pygame.draw.line(surface, (77, 83, 87), (0, y - ROAD_WIDTH // 2 - camera.y),
                             (WIDTH, y - ROAD_WIDTH // 2 - camera.y), 3)
            for x in range(0, WORLD.width, 72):
                pygame.draw.line(surface, (121, 114, 84), (x - camera.x, y - camera.y),
                                 (x + 35 - camera.x, y - camera.y), 2)
        for x in ROAD_X:
            rect = pygame.Rect(x - ROAD_WIDTH // 2 - camera.x, -camera.y, ROAD_WIDTH, WORLD.height)
            pygame.draw.rect(surface, (48, 56, 64), rect)
            pygame.draw.line(surface, (88, 94, 96), (x - ROAD_WIDTH // 2 - camera.x, 0),
                             (x - ROAD_WIDTH // 2 - camera.x, HEIGHT), 3)
            for y in range(0, WORLD.height, 72):
                pygame.draw.line(surface, (125, 116, 83), (x - camera.x, y - camera.y),
                                 (x - camera.x, y + 35 - camera.y), 2)
        for index, (rect, kind) in enumerate(BLOCKS):
            screen_rect = rect.move(-round(camera.x), -round(camera.y))
            if not screen_rect.colliderect(surface.get_rect()):
                continue
            if kind == "park":
                pygame.draw.rect(surface, (31, 79, 67), screen_rect, border_radius=12)
                for offset in range(3):
                    tree = (screen_rect.left + 60 + offset * 105, screen_rect.top + 65 + (offset % 2) * 80)
                    pygame.draw.circle(surface, (15, 48, 45), (tree[0] + 4, tree[1] + 5), 25)
                    pygame.draw.circle(surface, (56, 137, 99), tree, 24)
                continue
            pygame.draw.rect(surface, (12, 20, 29), screen_rect.move(9, 11), border_radius=7)
            shade = 43 + (index * 17) % 24
            pygame.draw.rect(surface, (shade, shade + 9, shade + 17), screen_rect, border_radius=7)
            pygame.draw.rect(surface, (93, 129, 143), screen_rect.inflate(-15, -15), 3, border_radius=4)
            for wx in range(screen_rect.left + 38, screen_rect.right - 20, 58):
                for wy in range(screen_rect.top + 38, screen_rect.bottom - 20, 54):
                    pygame.draw.rect(surface, (98, 177, 177) if (wx + wy + index) % 3 else (174, 155, 99),
                                     (wx, wy, 15, 10), border_radius=2)
        # Garage and collection marker are world objects; HUD points to off-screen targets.
        garage_rect = pygame.Rect(GARAGE.x - 55 - camera.x, GARAGE.y - 44 - camera.y, 110, 88)
        pygame.draw.rect(surface, (29, 101, 72), garage_rect, 3, border_radius=8)
        pygame.draw.rect(surface, (45, 87, 69), garage_rect.inflate(-12, -12), border_radius=5)

    def _objective(self, surface: pygame.Surface, city: City, camera: pygame.Vector2) -> None:
        if city.phase in ("won", "lost"):
            return
        point = pygame.Vector2(self._screen(city.objective, camera))
        color = GREEN if city.phase == "escape" else GOLD
        pulse = 5 * math.sin(city.elapsed * 5)
        if -45 < point.x < WIDTH + 45 and -45 < point.y < HEIGHT + 45:
            pygame.draw.circle(surface, color, point, round(42 + pulse), 3)
            pygame.draw.circle(surface, color, point, 16, 2)
            pygame.draw.line(surface, color, (point.x - 8, point.y), (point.x + 8, point.y), 2)
            pygame.draw.line(surface, color, (point.x, point.y - 8), (point.x, point.y + 8), 2)
        else:
            center = pygame.Vector2(WIDTH / 2, HEIGHT / 2)
            direction = point - center
            if direction.length_squared():
                direction.normalize_ip()
                edge = pygame.Vector2(clamp(point.x, 38, WIDTH - 38), clamp(point.y, 108, HEIGHT - 42))
                tip = edge + direction * 14
                side = pygame.Vector2(-direction.y, direction.x)
                pygame.draw.polygon(surface, color, [tip, edge - direction * 13 + side * 9,
                                                    edge - direction * 13 - side * 9])

    def _minimap(self, surface: pygame.Surface, city: City) -> None:
        panel = pygame.Rect(WIDTH - 210, 99, 185, 170)
        pygame.draw.rect(surface, (11, 24, 36), panel, border_radius=8)
        pygame.draw.rect(surface, (73, 109, 123), panel, 2, border_radius=8)
        sx, sy = (panel.width - 20) / WORLD.width, (panel.height - 20) / WORLD.height
        def dot(pos: pygame.Vector2) -> tuple[int, int]:
            return panel.left + 10 + round(pos.x * sx), panel.top + 10 + round(pos.y * sy)
        for x in ROAD_X:
            px = panel.left + 10 + round(x * sx)
            pygame.draw.line(surface, (53, 74, 81), (px, panel.top + 10), (px, panel.bottom - 10), 2)
        for y in ROAD_Y:
            py = panel.top + 10 + round(y * sy)
            pygame.draw.line(surface, (53, 74, 81), (panel.left + 10, py), (panel.right - 10, py), 2)
        pygame.draw.circle(surface, GREEN if city.phase == "escape" else GOLD, dot(city.objective), 5)
        for unit in city.police:
            pygame.draw.circle(surface, RED, dot(unit.pos), 3)
        pygame.draw.circle(surface, CYAN, dot(city.player), 5)
        pygame.draw.circle(surface, WHITE, dot(city.player), 5, 1)

    def draw(self, surface: pygame.Surface, city: City, controller: TiltController | None = None) -> None:
        camera = city.player - pygame.Vector2(WIDTH / 2, HEIGHT / 2)
        camera.x = clamp(camera.x, 0, WORLD.width - WIDTH)
        camera.y = clamp(camera.y, 0, WORLD.height - HEIGHT)
        self._world(surface, city, camera)
        self._objective(surface, city, camera)
        for car in city.cars:
            self._car(surface, car.pos, car.angle, car.color, camera)
            if car.target and city.phase == "steal":
                pygame.draw.circle(surface, GOLD, self._screen(car.pos, camera), 34, 2)
        for unit in city.police:
            self._car(surface, unit.pos, unit.angle, (221, 235, 241), camera, police=True)
        if city.current_car is None and (city.hit_cooldown == 0 or int(city.elapsed * 10) % 2 == 0):
            pygame.draw.circle(surface, (9, 27, 34), self._screen(city.player + pygame.Vector2(3, 4), camera), 15)
            pygame.draw.circle(surface, CYAN, self._screen(city.player, camera), 13)
            pygame.draw.circle(surface, WHITE, self._screen(city.player, camera), 6)
        self._minimap(surface, city)

        hud = pygame.Surface((WIDTH, 92), pygame.SRCALPHA)
        hud.fill((7, 16, 28, 236))
        surface.blit(hud, (0, 0))
        surface.blit(self.title.render("NEON CITY ESCAPE", True, WHITE), (25, 13))
        surface.blit(self.hud.render(city.objective_label, True, GREEN if city.phase == "escape" else GOLD), (26, 55))
        surface.blit(self.hud.render(f"CASH  ${city.cash}", True, WHITE), (540, 17))
        surface.blit(self.hud.render(f"ARMOR  {'|' * city.health}{'.' * (MAX_HEALTH - city.health)}", True, CYAN), (540, 54))
        surface.blit(self.hud.render(f"WANTED  {'*' * city.wanted}{'.' * (5 - city.wanted)}", True, RED), (780, 17))
        if city.phase not in ("won", "lost"):
            distance = round(city.player.distance_to(city.objective))
            surface.blit(self.small.render(f"WAYPOINT {distance}m", True, MUTED), (780, 59))
        footer = pygame.Surface((WIDTH, 56), pygame.SRCALPHA)
        footer.fill((7, 16, 28, 226))
        surface.blit(footer, (0, HEIGHT - 56))
        status = controller.message if controller else "ESP32 or keyboard"
        surface.blit(self.small.render(status[:49], True, CYAN if controller and controller.live else MUTED), (23, HEIGHT - 48))
        message = city.message if city.message_time > 0 else "Follow the waypoint and evade the police"
        surface.blit(self.small.render(message, True, GOLD), (23, HEIGHT - 26))
        surface.blit(self.small.render("TILT/WASD  DRIVE   E  ENTER/EXIT   SPACE  BRAKE", True, MUTED),
                     (480, HEIGHT - 46))
        surface.blit(self.small.render("C  CENTER   P  PAUSE   R  RESTART   M  MUTE   ESC  QUIT", True, MUTED),
                     (480, HEIGHT - 24))

        if city.paused or city.phase in ("won", "lost"):
            shade = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
            shade.fill((3, 10, 23, 192))
            surface.blit(shade, (0, 0))
            heading = "PAUSED" if city.paused else ("MISSION COMPLETE" if city.phase == "won" else "BUSTED")
            color = CYAN if city.paused else (GREEN if city.phase == "won" else RED)
            title = self.big.render(heading, True, color)
            surface.blit(title, title.get_rect(center=(WIDTH // 2, HEIGHT // 2 - 52)))
            detail = self.hud.render(f"PACKAGES {city.package_index}/3   CASH ${city.cash}", True, WHITE)
            surface.blit(detail, detail.get_rect(center=(WIDTH // 2, HEIGHT // 2 + 13)))
            prompt = "P to resume" if city.paused else "R or Enter to restart"
            label = self.small.render(prompt, True, MUTED)
            surface.blit(label, label.get_rect(center=(WIDTH // 2, HEIGHT // 2 + 62)))


def main() -> None:
    parser = argparse.ArgumentParser(description="Neon City Escape for ESP32 MPU6050")
    parser.add_argument("--port", help="Serial port, such as COM5 or /dev/ttyUSB0 (default: auto-detect)")
    parser.add_argument("--invert-roll", action="store_true", help="Reverse left/right tilt")
    parser.add_argument("--invert-pitch", action="store_true", help="Reverse forward/back tilt")
    args = parser.parse_args()

    pygame.mixer.pre_init(frequency=22050, size=-16, channels=1, buffer=512)
    pygame.init()
    screen = pygame.display.set_mode((WIDTH, HEIGHT))
    pygame.display.set_caption("Neon City Escape - ESP32 MPU6050")
    clock = pygame.time.Clock()
    city = City()
    renderer = Renderer()
    audio = Audio()
    controller = TiltController(args.port)
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
                    elif event.key == pygame.K_e:
                        before = city.phase
                        city.interact()
                        if before == "steal" and city.phase == "collect":
                            audio.play("pickup")
                    elif event.key == pygame.K_c:
                        controller.center()
                    elif event.key == pygame.K_m:
                        audio.muted = not audio.muted
                    elif event.key == pygame.K_p and city.phase not in ("won", "lost"):
                        city.paused = not city.paused
                    elif event.key == pygame.K_r or (event.key == pygame.K_RETURN and city.phase in ("won", "lost")):
                        city.reset()
            controller.update()
            keys = pygame.key.get_pressed()
            horizontal = deadzone(controller.roll) * (-1 if args.invert_roll else 1) if controller.live else 0.0
            vertical = -deadzone(controller.pitch) * (-1 if args.invert_pitch else 1) if controller.live else 0.0
            if keys[pygame.K_LEFT] or keys[pygame.K_a]:
                horizontal = -1.0
            if keys[pygame.K_RIGHT] or keys[pygame.K_d]:
                horizontal = 1.0
            if keys[pygame.K_UP] or keys[pygame.K_w]:
                vertical = -1.0
            if keys[pygame.K_DOWN] or keys[pygame.K_s]:
                vertical = 1.0
            city.update(dt, horizontal, vertical, keys[pygame.K_SPACE])
            for event_name in city.events:
                audio.play(event_name)
            renderer.draw(screen, city, controller)
            pygame.display.flip()
    finally:
        controller.close()
        pygame.quit()


if __name__ == "__main__":
    main()
