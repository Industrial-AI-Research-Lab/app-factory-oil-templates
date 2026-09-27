from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    Strict,
    StringConstraints,
    ValidationError,
    WithJsonSchema,
    model_serializer,
    model_validator,
)
from pydantic_core import core_schema

from app.config import (
    CALCULATOR_ROLE,
    FACET_ROLES,
    MAX_CALCULATORS,
    MAX_OUTPUTS,
    TEMPLATE_VERSIONS,
    allow_insecure_object_storage,
    has_calculators,
    has_facets,
    is_reader,
    reader_variant,
    upload_allowed_origins,
)
from app.rendering.render import CONTENT_TYPES

OutputFormat = Literal["txt", "json", "csv", "html", "pdf", "docx", "xlsx"]
TemplateId = Literal["news", "knowledge"]
TemplateVersion = Literal["1.0.0", "1.1.0", "1.2.0", "1.3.0", "1.4.0"]
SourceRole = Literal[
    "ontology", "fact_graph", "observations", "coverage", "schema_counts", "calculator"
]
OutputFilename = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z0-9А-Яа-яЁё_.-]{1,160}$"),
]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
NonNegativeInt = Annotated[int, Field(ge=0)]


def allowed_upload_url(url):
    default_port = 443 if url.scheme == "https" else 80
    origin = (
        url.scheme,
        url.host.rstrip(".").lower(),
        url.port or default_port,
    )
    scheme_is_allowed = url.scheme == "https" or (
        url.scheme == "http" and allow_insecure_object_storage()
    )
    if (
        not scheme_is_allowed
        or url.username is not None
        or url.password is not None
        or origin not in upload_allowed_origins()
    ):
        raise ValueError("Upload destination is not allowed")
    return url


UploadUrl = Annotated[
    AnyHttpUrl,
    AfterValidator(allowed_upload_url),
    WithJsonSchema({"type": "string", "format": "uri", "pattern": "^https?://"}),
]


def upload_destination(url):
    return url.scheme, url.host, url.port, url.path


class SafeRequestModel(BaseModel):
    @classmethod
    def __get_pydantic_core_schema__(cls, source, handler):
        schema = handler(source)
        return core_schema.no_info_wrap_validator_function(
            cls._validate_without_input,
            schema,
        )

    @classmethod
    def _validate_without_input(cls, value, handler):
        try:
            return handler(value)
        except ValidationError as exc:
            raise ValidationError.from_exception_data(
                cls.__name__,
                exc.errors(include_url=False, include_input=False),
                hide_input=True,
            ) from None


class OutputTarget(SafeRequestModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    format: OutputFormat
    filename: OutputFilename
    content_type: str
    upload_url: UploadUrl


class SourceArtifact(SafeRequestModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    url: UploadUrl
    expected_sha256: Annotated[Sha256, Strict()]
    role: SourceRole | None = None


class RenderReportRequest(SafeRequestModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    report_data: dict[str, Any]
    template_id: TemplateId
    template_version: TemplateVersion
    outputs: Annotated[list[OutputTarget], Field(min_length=1, max_length=MAX_OUTPUTS)]
    manifest_upload_url: UploadUrl
    expected_result_sha256: Annotated[Sha256, Strict()] | None = None
    source_artifacts: (
        Annotated[list[SourceArtifact], Field(min_length=1, max_length=20)] | None
    ) = None
    expected_collection_sha256: Annotated[Sha256, Strict()] | None = None

    @model_validator(mode="after")
    def unique_formats(self):
        if self.template_version not in TEMPLATE_VERSIONS[self.template_id]:
            raise ValueError("Template version is not available for this template")
        if self.source_artifacts:
            hashes = [source.expected_sha256 for source in self.source_artifacts]
            if len(hashes) != len(set(hashes)):
                raise ValueError("Source artifacts must be unique")
            if (
                self.template_id == "knowledge"
                and self.expected_collection_sha256 is None
            ):
                raise ValueError("Knowledge sources require a collection checksum")
            if self.template_id == "news" and len(hashes) != 1:
                raise ValueError("News requires one canonical result")
            validate_source_roles(
                self.template_id, self.template_version, self.source_artifacts
            )
        elif self.expected_collection_sha256 is not None:
            raise ValueError("Collection checksum requires source artifacts")
        elif reader_variant(self.template_id, self.template_version) is not None:
            raise ValueError("Reader templates are built from source artifacts")
        formats = [target.format for target in self.outputs]
        if len(formats) != len(set(formats)):
            raise ValueError("Output formats must be unique")
        destinations = [
            upload_destination(target.upload_url) for target in self.outputs
        ]
        if len(destinations) != len(set(destinations)):
            raise ValueError("Output upload destinations must be unique")
        if upload_destination(self.manifest_upload_url) in set(destinations):
            raise ValueError("Manifest upload destination must be unique")
        source_destinations = {
            upload_destination(source.url) for source in self.source_artifacts or []
        }
        if (
            source_destinations.intersection(destinations)
            or upload_destination(self.manifest_upload_url) in source_destinations
        ):
            raise ValueError("Report outputs cannot overwrite source artifacts")
        for target in self.outputs:
            if CONTENT_TYPES.get(target.format) != target.content_type:
                raise ValueError("Output content type does not match its format")
        return self


def validate_source_roles(template_id, template_version, sources):
    roles = [source.role for source in sources if source.role is not None]
    if not roles:
        return
    if template_id != "knowledge" or not is_reader("knowledge", template_version):
        raise ValueError("Source roles require the knowledge reader template")
    calculators = [role for role in roles if role == CALCULATOR_ROLE]
    single = [role for role in roles if role != CALCULATOR_ROLE]
    if len(single) != len(set(single)):
        raise ValueError("Each source role may be used once")
    if len(roles) == len(sources):
        raise ValueError("Knowledge reports require a query result")
    if set(roles) & set(FACET_ROLES) and not has_facets(template_id, template_version):
        raise ValueError("Facet roles require knowledge 1.4.0")
    if len(calculators) > MAX_CALCULATORS:
        raise ValueError("Knowledge reports accept at most four calculator artifacts")
    if calculators and not has_calculators(template_id, template_version):
        raise ValueError("Calculator role requires knowledge 1.4.0")


class ResultSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    version: str
    sha256: Sha256


class TemplateSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: TemplateId
    version: TemplateVersion


class FormatError(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    stage: Literal["render", "upload"]
    message: str


class FormatResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["failed", "generated", "ready"]
    filename: str
    content_type: str
    size_bytes: NonNegativeInt
    sha256: Sha256 | None
    error: FormatError | None = None

    @model_validator(mode="after")
    def valid_status_fields(self):
        if self.status == "failed":
            if self.error is None:
                raise ValueError("Failed format requires an error")
            if self.error.stage == "render":
                if self.sha256 is not None or self.size_bytes != 0:
                    raise ValueError("Render failure cannot contain generated bytes")
            elif self.sha256 is None or self.size_bytes == 0:
                raise ValueError("Upload failure requires generated bytes")
            return self
        if self.error is not None:
            raise ValueError("Successful format cannot contain an error")
        if self.sha256 is None or self.size_bytes == 0:
            raise ValueError("Generated format requires bytes and SHA-256")
        return self


class SourceDigest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sha256: Sha256


class RenderReportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    project_id: str
    run_id: str
    result: ResultSnapshot
    template: TemplateSelection
    template_snapshot: dict[str, Sha256]
    formats: dict[OutputFormat, FormatResult]
    source_artifacts: list[SourceDigest] = Field(default_factory=list)
    collection_sha256: Sha256 | None = None
    ontology_sha256: Sha256 | None = None
    fact_graph_sha256: Sha256 | None = None
    facet_sha256s: dict[str, Sha256] | None = None
    calculator_sha256s: dict[str, Sha256] | None = None

    @model_serializer(mode="wrap")
    def omit_absent_role_checksums(self, handler):
        # Responses of earlier template versions must keep their exact shape.
        data = handler(self)
        for key in (
            "ontology_sha256",
            "fact_graph_sha256",
            "facet_sha256s",
            "calculator_sha256s",
        ):
            if data.get(key) is None:
                data.pop(key, None)
        return data
