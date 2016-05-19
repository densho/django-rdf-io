from django.db import models
from django.utils.translation import ugettext_lazy as _
# for django 1.7 +
#from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.generic import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator, URLValidator
from django.db.models.fields.related import ReverseSingleRelatedObjectDescriptor
import itertools

# helpers
def getattr_path(obj,path) :
    try :
        return _getattr_related(obj, path.replace('__','.').replace("/",".").split('.'))
        
    except ValueError as e:
        import traceback
#        import pdb; pdb.set_trace()
        raise ValueError("Failed to map '{}' on '{}' (cause {})".format(path, obj, e))
        
def _apply_filter(val, filter) :
    """
        Apply a simple filter to a specific property, with a list of possible values
    """
    for targetvel in filter.replace(" OR ",",").split(",") :
        if val == targetvel :
            return True
    return False
    
def _getattr_related(obj, fields):
    """
        get an attribute - if multi-valued will be a list object!
        fields may include filters.  
    """
    # print obj, fields
    if not len(fields):
        return [obj]
        
    field = fields.pop(0)
    filter = None
    # try to get - then check for django 1.7+ manager for related field
    try:
        # check for lang 
        try:
            (field,langfield) = field.split('@')
            if langfield[0] in ["'" , '"'] :
                lang = langfield[1:-1]
            else:
                lang = _getattr_related(obj, [langfield,] + fields).pop(0)
        except:
            lang = None
        # check for filter 
        if "[" in field :
            filter = field[ field.index("[") +1 : -1 ]
            field = field[0:field.index("[")]
           
        a = getattr(obj, field)
        if filter and not _apply_filter(a, filter) :
            return []
        if lang:
            a = "@".join((a,lang))
    except AttributeError:
        # then try to find objects of this type with a foreign key
        try:
            reltype = ContentType.objects.get(model=field)
        except ContentType.DoesNotExist as e :
            raise ValueError("Could not locate attribute or related model '{}' in element '{}'".format(field, type(obj)) )
        # id django 1.7+ we could just use field_set to get a manager :-(
        claz = reltype.model_class()
        import pdb; pdb.set_trace()
        for prop,val in claz.__dict__.items() :
            if type(val) is ReverseSingleRelatedObjectDescriptor and val.field.related.model == claz :
                filters = {prop : obj}
                if filter :
                    filters.update(dict( [fc.split("=") for fc in filter.replace(" AND ",",").split(",")]))
                a = claz.objects.filter(**filters)
                break
    
    try:
        # slice the list fo fields[:] to force a copy so each iteration starts from top of list in spite of pop()
        return itertools.chain(*(_getattr_related(xx, fields[:]) for xx in a.all()))
#        !list(itertools.chain(*([[1],[2]])))
    except:
        return _getattr_related(a, fields)
        
def validate_urisyntax(value):

    if value[0:4] == 'http' :
        URLValidator(verify_exists=False).__call__(value)
    else :
        parts = value.split(str=":")
        if len(parts) != 2 :
            raise ValidationError('invalid syntax')
        ns = Namespace.objects.get(prefix=part[0])
    
class CURIE_Field(models.CharField):
    """
        Char field for URI with syntax checking for CURIE or http syntax
    """
    # validate that prefix is registered if used
    validators = [ validate_urisyntax, ]
    def __init__(self, *args, **kwargs):
        kwargs['max_length'] = 200
        kwargs['help_text']=_(u'use a:b or full URI')
        super( CURIE_Field, self).__init__(*args, **kwargs)
    
class EXPR_Field(models.CharField):
    """
        Char field for expression - literal or nested atribute with syntax checking for CURIE or http syntax
    """
    literal_form=None
    def __init__(self, *args, **kwargs):
        kwargs['max_length'] = 400
        kwargs['help_text']=_(u'for a literal, use "quoted" syntax, for nested attribute use syntax a.b.c')
        super( EXPR_Field, self).__init__(*args, **kwargs)


class FILTER_Field(models.CharField):
    """
        Char field for filter expression:  path=value(,value)
    """

    def __init__(self, *args, **kwargs):
        kwargs['max_length'] = 400
        kwargs['help_text']=_(u'path=value, eg label__label_text="frog"')
        super( FILTER_Field, self).__init__(*args, **kwargs)
        
    
# Need natural keys so can reference in fixtures - let this be the uri

class NamespaceManager(models.Manager):
    def get_by_natural_key(self, uri):
        return self.get(uri=uri)

class Namespace(models.Model) :
    """
        defines a namespace so we can use short prefix where convenient 
    """
    objects = NamespaceManager()
    
    uri = models.CharField('uri',max_length=100, unique=True, null=False)
    prefix = models.CharField('prefix',max_length=8,unique=True,null=False)
    notes = models.TextField(_(u'change note'),blank=True)

    def natural_key(self):
        return(self.uri)
    
    def get_base_uri(self):
        return self.uri[0:-1]
    def is_hash_uri(self):
        return self.uri[-1] == '#'
        
    class Meta: 
        verbose_name = _(u'namespace')
        verbose_name_plural = _(u'namespaces')
    def __unicode__(self):
        return self.uri    

class GenericMetaPropManager(models.Manager):
    def get_by_natural_key(self, curie):
        try:
            (namespace,prop) = curie.split(":")
        except:
            pass
        return self.get(namespace__prefix=namespace, propname=prop)
        
class GenericMetaProp(models.Model) :
    """
        a metadata property that can be attached to any target model to provide extensible metadata.
        Works with the namespace object to allow short forms of metadata to be displayed
    """
    objects = GenericMetaPropManager()
    namespace = models.ForeignKey(Namespace,verbose_name=_(u'namespace'))
    propname =  models.CharField(_(u'name'),blank=False,max_length=250,editable=True)
    definition  = models.TextField(_(u'definition'), blank=True)
    def natural_key(self):
        return ":".join((self.namespace.prefix,self.propname))
    def __unicode__(self):              # __unicode__ on Python 2
        return self.natural_key() 
 
        
class ObjectTypeManager(models.Manager):
    def get_by_natural_key(self, uri):
        return self.get(uri=uri)
        
class ObjectType(models.Model):
    """
        Allows for a target object to be declared as multiple object types
        Object types may be URI or CURIEs using declared prefixes
    """
    objects = ObjectTypeManager()
    uri = CURIE_Field(_(u'URI'),blank=False,editable=True)
    label = models.CharField(_(u'Label'),blank=False,max_length=250,editable=True)
    
    def natural_key(self):
        return self.uri
    
    # check short form is registered
    def __unicode__(self):              # __unicode__ on Python 2
        return self.label 

class ObjectMappingManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)
                
class ObjectMapping(models.Model):
    """
        Maps an instance of a model to a resource (i.e. a URI with a type declaration) 
    """
    objects = ObjectMappingManager()
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    name = models.CharField(_(u'Name'),help_text=_(u'unique identifying label'),unique=True,blank=False,max_length=250,editable=True)
    id_attr = models.CharField(_(u'ID Attribute'),help_text=_(u'for nested attribute use syntax a.b.c'),blank=False,max_length=250,editable=True)
    target_uri_expr = EXPR_Field(_(u'target namespace expression'), blank=False,editable=True)
    obj_type = models.ManyToManyField(ObjectType)
    filter = FILTER_Field(_(u'Filter'), null=True, blank=True ,editable=True)
    def natural_key(self):
        return self.name    
  
    def __unicode__(self):              # __unicode__ on Python 2
        return self.name 
 

class AttributeMapping(models.Model):
    """
        records a mapping from an object mapping that defines the object to a value using a predicate
    """
    scope = models.ForeignKey(ObjectMapping)
    attr = EXPR_Field(_(u'source attribute'),blank=False,editable=True)
    filter = FILTER_Field(_(u'Filter'), null=True, blank=True,editable=True)
    predicate = CURIE_Field(_(u'predicate'),blank=False,editable=True)
    is_resource = models.BooleanField(_(u'as URI'))
    
    def __unicode__(self):
        return ( ' '.join((self.attr, self.predicate )))