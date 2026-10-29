"""Bounded user-selected document context; browser renderings are not measurements."""
import base64
import re
from typing import Annotated, Literal
from pydantic import Field, model_validator
from .schemas import APIModel
from .errors import APIError

class IfcElementSelection(APIModel):
    express_id: int = Field(ge=1)
    global_id: str | None = Field(default=None,max_length=100)
    label: str = Field(max_length=500)
    properties: dict[str,str] = Field(default_factory=dict,max_length=12)

    @model_validator(mode='after')
    def bounded_properties(self):
        if any(len(k)>80 or len(v)>500 for k,v in self.properties.items()):
            raise ValueError('Properties exceed context limits')
        return self

class DocumentSelection(APIModel):
    file_id: str = Field(min_length=1, max_length=150)
    kind: Literal['region', 'ifc']
    label: str = Field(min_length=1, max_length=500)
    page: int | None = Field(default=None, ge=1, le=10000)
    bbox: list[Annotated[float, Field(ge=0,le=1,allow_inf_nan=False)]] | None = Field(default=None,min_length=4,max_length=4)
    express_id: int | None = Field(default=None, ge=1)
    global_id: str | None = Field(default=None,max_length=100)
    properties: dict[str,str] = Field(default_factory=dict,max_length=12)
    elements: list[IfcElementSelection] = Field(default_factory=list,max_length=20)
    image_data: str | None = Field(default=None,max_length=350000)

    @model_validator(mode='after')
    def valid_selection(self):
        if self.kind=='region':
            if not self.page or not self.bbox or self.express_id is not None or self.elements:
                raise ValueError('Region requires a page and normalized bounding box')
            x,y,w,h=self.bbox
            if w<=0 or h<=0 or x+w>1.000001 or y+h>1.000001:
                raise ValueError('Invalid region bounds')
        elif not self.express_id or self.bbox is not None:
            raise ValueError('IFC selection requires express_id')
        if self.elements and (self.elements[0].express_id!=self.express_id or len({e.express_id for e in self.elements})!=len(self.elements)):
            raise ValueError('Invalid IFC selection set')
        if any(len(k)>80 or len(v)>500 for k,v in self.properties.items()):
            raise ValueError('Properties exceed context limits')
        if self.image_data:
            prefix='data:image/jpeg;base64,'
            if not self.image_data.startswith(prefix):raise ValueError('Selection image must be JPEG')
            try: raw=base64.b64decode(self.image_data[len(prefix):],validate=True)
            except ValueError:raise ValueError('Invalid image encoding') from None
            if not raw.startswith(b'\xff\xd8\xff') or not raw.endswith(b'\xff\xd9'):
                raise ValueError('Invalid JPEG image')
        return self

def selection_context(service,connection,principal,chat_id,request):
    selection=request.document_selection
    if selection is None:return None
    if selection.file_id not in request.file_ids:
        raise APIError(422,'selection_file_required','Select the document file together with its region or element')
    file=service.workspace.files(connection,principal,chat_id).get(selection.file_id)
    if not file or file['deleted']:
        raise APIError(422,'file_unavailable','The document is unavailable')
    context=selection.model_dump(exclude={'image_data'},exclude_none=True)
    context.update(file_name=file['name'],source_sha256=file.get('sha256'),
                   provenance='User selection in browser viewer. Preview and properties are user-provided reference data, not independently verified measurements.')
    if selection.kind=='ifc':
        if not file['name'].lower().endswith('.ifc') or not file.get('sha256'):
            raise APIError(422,'invalid_document_selection','IFC selection requires an IFC source')
        # Bind selected STEP identity to the actual stored file, not arbitrary browser metadata.
        raw=service.bim_store.path_for(file['sha256']).read_text(encoding='utf-8-sig',errors='replace')
        def bound_element(element):
            match=re.search(r'#'+str(element.express_id)+r'\s*=\s*(IFC[A-Z0-9_]+)\s*\((.*?);',raw,re.S)
            if not match:raise APIError(422,'invalid_document_selection','Selected IFC element is absent from this file')
            guid=re.match(r"\s*'([^']*)'",match.group(2))
            if element.global_id and (not guid or guid.group(1)!=element.global_id):
                raise APIError(422,'invalid_document_selection','IFC GlobalId does not match the source')
            return {'express_id':element.express_id,'global_id':element.global_id,'label':element.label,
                    'properties':element.properties,'source_entity':match.group(0)[:2000],'entity_type':match.group(1)}
        elements=[bound_element(element) for element in (selection.elements or [selection])]
        context['elements']=elements
        context['source_entity']=elements[0]['source_entity']
        context['entity_type']=elements[0]['entity_type']
    elif not file['name'].lower().endswith(('.pdf','.png','.jpg','.jpeg','.webp')):
        raise APIError(422,'invalid_document_selection','Region selection requires PDF or an image')
    return context
