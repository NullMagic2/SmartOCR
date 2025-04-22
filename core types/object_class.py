@dataclass
class ObjectType():
    
    index: int = 0
    page: Optional[int] = None
    text: Optional[str] = None
    coordinates: List[int] = field(default_factory = lambda: [0, 0, 0, 0])
    image_content: = None 